import { useState, useEffect, useCallback, useRef, type ChangeEvent } from "react";
import { MainLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import {
  Save, ChevronRight, ChevronDown,
  Trash2, Edit3, Sparkles, RefreshCw, ImageIcon, Loader2, Pause, Download, Upload, FileText, ClipboardPaste,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useUpload } from "@/contexts/UploadContext";
import {
  uploadApi, promptApi, toFileUrl,
  type PromptBulkItemPayload, type PromptItem, type ProgressInfo, type PromptLibraryPayload, type BackupPayload,
} from "@/lib/api";

// ========== 类型 & 常量 ==========

interface BaseImageInfo {
  id: string;
  filename: string;
  thumbnail: string;
  status: string;
}

/** 人群类型（与后端 constants.py 保持一致） */
const crowdTypes = {
  single: [
    { id: "C01", name: "幼女", desc: "4-12岁女童" },
    { id: "C02", name: "少女", desc: "12-28岁女性" },
    { id: "C03", name: "熟女", desc: "28-50岁女性" },
    { id: "C04", name: "奶奶", desc: "50岁以上女性" },
    { id: "C05", name: "幼男", desc: "4-12岁男童" },
    { id: "C06", name: "少男", desc: "12-45岁男性" },
    { id: "C07", name: "大叔", desc: "45岁以上男性" },
  ],
  combo: [
    { id: "C08", name: "情侣", desc: "年轻男女" },
    { id: "C09", name: "闺蜜", desc: "女性好友" },
    { id: "C10", name: "兄弟", desc: "男性好友" },
    { id: "C11", name: "异性伙伴", desc: "异性朋友" },
    { id: "C12", name: "母子(少年)", desc: "母亲+少年儿子" },
    { id: "C13", name: "母子(青年)", desc: "母亲+青年儿子" },
    { id: "C14", name: "母女(少年)", desc: "母亲+少年女儿" },
    { id: "C15", name: "母女(青年)", desc: "母亲+青年女儿" },
    { id: "C16", name: "父子(少年)", desc: "父亲+少年儿子" },
    { id: "C17", name: "父子(青年)", desc: "父亲+青年儿子" },
    { id: "C18", name: "父女(少年)", desc: "父亲+少年女儿" },
    { id: "C19", name: "父女(青年)", desc: "父亲+青年女儿" },
  ],
};

const allTypes = [...crowdTypes.single, ...crowdTypes.combo];

/** 按 crowd_type 分组 PromptItem[] */
function groupByCrowdType(prompts: PromptItem[]): Record<string, PromptItem[]> {
  const map: Record<string, PromptItem[]> = {};
  for (const p of prompts) {
    (map[p.crowd_type] ??= []).push(p);
  }
  return map;
}

type PromptDraft = Partial<Pick<PromptItem, "positive_prompt" | "negative_prompt" | "style_name">>;
type ParsedFlexibleImportResult = {
  items: PromptBulkItemPayload[];
  errors: string[];
};

function buildPromptLibraryFilename(exportedAt?: string) {
  const date = exportedAt ? new Date(exportedAt) : new Date();
  const safeDate = Number.isNaN(date.getTime()) ? new Date() : date;
  const pad = (value: number) => String(value).padStart(2, "0");
  return `prompt-library_${safeDate.getFullYear()}${pad(safeDate.getMonth() + 1)}${pad(safeDate.getDate())}_${pad(safeDate.getHours())}${pad(safeDate.getMinutes())}${pad(safeDate.getSeconds())}.json`;
}

function isPromptLibraryPayload(value: unknown): value is PromptLibraryPayload {
  if (!value || typeof value !== "object") return false;
  const data = value as Partial<PromptLibraryPayload>;
  return (
    typeof data.version === "string" &&
    typeof data.exported_at === "string" &&
    typeof data.app_name === "string" &&
    Array.isArray(data.prompts)
  );
}

function isBackupPayload(value: unknown): value is BackupPayload {
  if (!value || typeof value !== "object") return false;
  const data = value as Partial<BackupPayload>;
  return (
    typeof data.version === "string" &&
    typeof data.exported_at === "string" &&
    typeof data.app_name === "string" &&
    Array.isArray(data.settings) &&
    Array.isArray(data.prompt_templates)
  );
}

function normalizeImportedPromptLibrary(value: unknown): PromptLibraryPayload | null {
  if (isPromptLibraryPayload(value)) return value;
  if (!isBackupPayload(value)) return null;
  return {
    version: "prompt-library.v1",
    exported_at: value.exported_at,
    app_name: value.app_name,
    prompts: value.prompt_templates.map((item) => ({
      id: item.id,
      crowd_type: item.crowd_type,
      style_name: item.style_name,
      positive_prompt: item.positive_prompt,
      negative_prompt: item.negative_prompt,
      reference_weight: item.reference_weight,
      preferred_engine: item.preferred_engine,
      is_active: item.is_active,
      create_time: item.create_time,
    })),
    summary: {
      prompt_count: value.prompt_templates.length,
      crowd_type_count: new Set(value.prompt_templates.map((item) => item.crowd_type)).size,
    },
  };
}

function parseFlexibleImportText(text: string, stylePrefix: string): ParsedFlexibleImportResult {
  const content = text.replace(/\r\n/g, "\n").trim();
  if (!content) {
    return { items: [], errors: ["请输入要导入的提示词内容"] };
  }

  try {
    const parsed = JSON.parse(content);

    if (isPromptLibraryPayload(parsed) || isBackupPayload(parsed)) {
      return {
        items: [],
        errors: ["检测到整库备份 JSON，请改用「备份还原」入口"],
      };
    }

    if (Array.isArray(parsed)) {
      const items: PromptBulkItemPayload[] = [];
      for (let index = 0; index < parsed.length; index += 1) {
        const row = parsed[index];
        if (!row || typeof row !== "object") continue;
        const data = row as Record<string, unknown>;
        const styleName = String(data.style_name || `模板${String(index + 1).padStart(2, "0")}`).trim();
        const positive = String(data.positive_prompt || data.positive || data.prompt || "").trim();
        const negative = String(data.negative_prompt || data.negative || "").trim();
        if (!positive) continue;
        items.push({
          style_name: styleName,
          positive_prompt: positive,
          negative_prompt: negative,
          reference_weight: 90,
          preferred_engine: "seedream",
          is_active: true,
        });
      }
      if (items.length > 0) {
        return { items, errors: [] };
      }
    }

    if (parsed && typeof parsed === "object") {
      const obj = parsed as Record<string, unknown>;
      const rows = Array.isArray(obj.rows) ? obj.rows : null;
      if (rows) {
        const items: PromptBulkItemPayload[] = [];
        rows.forEach((raw, index) => {
          if (!raw || typeof raw !== "object") return;
          const row = raw as Record<string, unknown>;
          const styleName = String(row.style_name || `模板${String(index + 1).padStart(2, "0")}`).trim();
          const positive = String(row.positive_prompt || row.positive || row.prompt || "").trim();
          const negative = String(row.negative_prompt || row.negative || "").trim();
          if (!positive) return;
          items.push({
            style_name: styleName,
            positive_prompt: positive,
            negative_prompt: negative,
            reference_weight: 90,
            preferred_engine: "seedream",
            is_active: true,
          });
        });
        if (items.length > 0) {
          return { items, errors: [] };
        }
      } else {
        const items: PromptBulkItemPayload[] = [];
        Object.entries(obj).forEach(([key, raw]) => {
          if (!raw) return;
          if (typeof raw === "string") {
            const positive = raw.trim();
            if (!positive) return;
            items.push({
              style_name: key.trim(),
              positive_prompt: positive,
              negative_prompt: "",
              reference_weight: 90,
              preferred_engine: "seedream",
              is_active: true,
            });
            return;
          }
          if (typeof raw === "object") {
            const row = raw as Record<string, unknown>;
            const positive = String(row.positive_prompt || row.positive || row.prompt || row["正向"] || "").trim();
            const negative = String(row.negative_prompt || row.negative || row["负向"] || "").trim();
            if (!positive) return;
            items.push({
              style_name: key.trim(),
              positive_prompt: positive,
              negative_prompt: negative,
              reference_weight: 90,
              preferred_engine: "seedream",
              is_active: true,
            });
          }
        });
        if (items.length > 0) {
          return { items, errors: [] };
        }
      }
    }
  } catch {
    // ignore JSON parse errors and continue to plain text syntaxes
  }

  const lines = content.split("\n").map((line) => line.trim()).filter(Boolean);
  const errors: string[] = [];

  if (
    lines.length >= 2
    && lines[0].includes("|")
    && /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?$/.test(lines[1])
  ) {
    const headers = lines[0]
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((part) => part.trim().toLowerCase());
    const items: PromptBulkItemPayload[] = [];
    for (let index = 2; index < lines.length; index += 1) {
      const line = lines[index];
      if (!line.includes("|")) continue;
      const cols = line
        .replace(/^\|/, "")
        .replace(/\|$/, "")
        .split("|")
        .map((part) => part.trim());
      const row: Record<string, string> = {};
      headers.forEach((header, idx) => {
        row[header] = cols[idx] || "";
      });
      const styleName = (
        row["style_name"]
        || row.style
        || row["模板名"]
        || row["模板"]
        || row["名称"]
        || `${stylePrefix}${String(items.length + 1).padStart(2, "0")}`
      ).trim();
      const positive = (
        row["positive_prompt"]
        || row.positive
        || row.prompt
        || row["正向提示词"]
        || row["正向"]
        || row["提示词"]
        || ""
      ).trim();
      const negative = (
        row["negative_prompt"]
        || row.negative
        || row["负向提示词"]
        || row["负向"]
        || ""
      ).trim();
      if (!positive) {
        errors.push(`Markdown 第 ${index + 1} 行正向提示词为空`);
        continue;
      }
      items.push({
        style_name: styleName,
        positive_prompt: positive,
        negative_prompt: negative,
        reference_weight: 90,
        preferred_engine: "seedream",
        is_active: true,
      });
    }
    return { items, errors };
  }

  const tableSep = lines.some((line) => line.includes("\t"))
    ? "\t"
    : (lines.some((line) => line.includes("|")) ? "|" : "");
  if (tableSep) {
    const items: PromptBulkItemPayload[] = [];
    const headerCells = lines[0]?.split(tableSep).map((part) => part.trim().toLowerCase()) ?? [];
    const knownHeaderTokens = new Set([
      "style_name",
      "style",
      "模板名",
      "模板",
      "名称",
      "positive_prompt",
      "positive",
      "prompt",
      "正向提示词",
      "正向",
      "提示词",
      "negative_prompt",
      "negative",
      "负向提示词",
      "负向",
    ]);
    const hasHeader = headerCells.length >= 2
      && headerCells.every((cell) => knownHeaderTokens.has(cell));
    const dataLines = hasHeader ? lines.slice(1) : lines;

    for (let index = 0; index < dataLines.length; index += 1) {
      const cols = dataLines[index].split(tableSep).map((part) => part.trim());
      if (cols.length < 2) {
        errors.push(`第 ${index + 1} 行列数不足，至少需要“模板名 + 正向提示词”`);
        continue;
      }
      const styleName = cols[0] || `${stylePrefix}${String(index + 1).padStart(2, "0")}`;
      const positive = cols[1] || "";
      const negative = cols[2] || "";
      if (!positive) {
        errors.push(`第 ${index + 1} 行正向提示词为空`);
        continue;
      }
      items.push({
        style_name: styleName,
        positive_prompt: positive,
        negative_prompt: negative,
        reference_weight: 90,
        preferred_engine: "seedream",
        is_active: true,
      });
    }
    return { items, errors };
  }

  if (content.includes("\n---")) {
    const blocks = content.split(/\n---+\n/g).map((block) => block.trim()).filter(Boolean);
    const items: PromptBulkItemPayload[] = [];
    blocks.forEach((block, index) => {
      const blockLines = block.split("\n").map((line) => line.trim()).filter(Boolean);
      if (blockLines.length === 0) return;
      const styleName = blockLines[0] || `${stylePrefix}${String(index + 1).padStart(2, "0")}`;
      let positive = "";
      let negative = "";
      for (const line of blockLines.slice(1)) {
        const lower = line.toLowerCase();
        if (lower.startsWith("正向:") || lower.startsWith("positive:")) {
          positive = line.replace(/^正向:|^positive:/i, "").trim();
        } else if (lower.startsWith("负向:") || lower.startsWith("negative:")) {
          negative = line.replace(/^负向:|^negative:/i, "").trim();
        } else if (!positive) {
          positive = line;
        } else if (!negative) {
          negative = line;
        }
      }
      if (!positive) {
        errors.push(`第 ${index + 1} 块缺少正向提示词`);
        return;
      }
      items.push({
        style_name: styleName,
        positive_prompt: positive,
        negative_prompt: negative,
        reference_weight: 90,
        preferred_engine: "seedream",
        is_active: true,
      });
    });
    return { items, errors };
  }

  return {
    items: lines.map((line, index) => ({
      style_name: `${stylePrefix}${String(index + 1).padStart(2, "0")}`,
      positive_prompt: line,
      negative_prompt: "",
      reference_weight: 90,
      preferred_engine: "seedream" as const,
      is_active: true,
    })),
    errors,
  };
}


// ========== 组件 ==========

export default function PromptConfig() {
  const { batchId } = useUpload();

  // --- 底图列表 ---
  const [images, setImages] = useState<BaseImageInfo[]>([]);
  const [selectedImageId, setSelectedImageId] = useState<string>("");

  // --- 提示词数据 ---
  const [promptMap, setPromptMap] = useState<Record<string, PromptItem[]>>({});
  const [expandedType, setExpandedType] = useState<string | null>("C02");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [promptCount, setPromptCount] = useState(5);

  // --- 生成进度 ---
  const [isGenerating, setIsGenerating] = useState(false);
  const [genProgress, setGenProgress] = useState<ProgressInfo | null>(null);
  const [isExportingLibrary, setIsExportingLibrary] = useState(false);
  const [isImportingLibrary, setIsImportingLibrary] = useState(false);
  const [isFlexibleImportSubmitting, setIsFlexibleImportSubmitting] = useState(false);
  const [isCreatingTasks, setIsCreatingTasks] = useState(false);
  const [isBackupDialogOpen, setIsBackupDialogOpen] = useState(false);
  const [isFlexibleImportDialogOpen, setIsFlexibleImportDialogOpen] = useState(false);
  const [selectedImportFileName, setSelectedImportFileName] = useState("");
  const [pendingImportLibrary, setPendingImportLibrary] = useState<PromptLibraryPayload | null>(null);
  const [flexibleImportText, setFlexibleImportText] = useState("");
  const [flexibleImportFileName, setFlexibleImportFileName] = useState("");
  const [replaceCurrentType, setReplaceCurrentType] = useState(false);

  // --- 加载状态 ---
  const [loadingImages, setLoadingImages] = useState(false);
  const [loadingPrompts, setLoadingPrompts] = useState(false);

  // 防抖保存 timer
  const saveTimerRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const pendingDraftRef = useRef<Record<string, PromptDraft>>({});
  const backupImportFileRef = useRef<HTMLInputElement>(null);
  const flexibleImportFileRef = useRef<HTMLInputElement>(null);

  // ========== 初始化：加载底图列表 ==========

  useEffect(() => {
    if (!batchId) {
      setImages([]);
      setSelectedImageId("");
      return;
    }

    let cancelled = false;
    (async () => {
      setLoadingImages(true);
      const detail = await uploadApi.getBatch(batchId);
      if (cancelled) return;
      if (detail?.images) {
        const completed = detail.images
          .filter((img) => img.status === "completed")
          .map((img) => ({
            id: img.id,
            filename: img.filename,
            thumbnail: toFileUrl(img.processed_path || img.original_path),
            status: img.status,
          }));
        setImages(completed);
        if (completed.length > 0) setSelectedImageId(completed[0].id);
      }
      setLoadingImages(false);
    })();
    return () => { cancelled = true; };
  }, [batchId]);

  // ========== 初始化：加载提示词列表 ==========

  const loadPrompts = useCallback(async () => {
    setLoadingPrompts(true);
    const result = await promptApi.list({ batch_id: batchId || undefined });
    if (result) {
      setPromptMap(groupByCrowdType(result.prompts));
    }
    setLoadingPrompts(false);
  }, [batchId]);

  useEffect(() => {
    loadPrompts();
  }, [loadPrompts]);

  // ========== 生成当前选中类型 ==========

  const pollGenerateProgress = useCallback(async (bid: string) => {
    const POLL_INTERVAL = 2000;
    const MAX_POLLS = 300;

    let polls = 0;
    while (polls < MAX_POLLS) {
      await new Promise((r) => setTimeout(r, POLL_INTERVAL));
      polls++;

      const info = await promptApi.progress(bid);
      if (!info) break;

      setGenProgress(info);

      if (info.status === "completed" || info.status === "error" || info.status === "cancelled") {
        return info;
      }
    }
    return null;
  }, []);

  // ========== 刷新恢复：检查是否有正在运行的提示词生成任务 ==========
  useEffect(() => {
    if (!batchId) return;
    let cancelled = false;
    (async () => {
      try {
        const info = await promptApi.progress(batchId);
        if (cancelled) return;
        if (info && info.status === "running") {
          setIsGenerating(true);
          setGenProgress(info);
          const finalInfo = await pollGenerateProgress(batchId);
          if (cancelled) return;
          setIsGenerating(false);
          if (finalInfo?.status === "completed") {
            toast.success("提示词生成完成！");
            loadPrompts();
          } else if (finalInfo?.status === "cancelled") {
            toast.info("提示词生成已中断");
            loadPrompts();
          } else {
            toast.error("提示词生成失败或超时");
          }
        }
      } catch { /* ignore */ }
    })();
    return () => { cancelled = true; };
  }, [batchId, pollGenerateProgress, loadPrompts]);

  const handleGenerateSelected = useCallback(async () => {
    if (!batchId) {
      toast.info("演示模式：无法调用后端生成");
      return;
    }
    if (!expandedType) {
      toast.info("请先选择一个人群类型");
      return;
    }
    if (!crowdTypes.single.some((t) => t.id === expandedType)) {
      toast.info("当前版本仅支持单人7类，组合人群暂未开放");
      return;
    }

    const normalizedPromptCount = Math.max(1, Math.min(20, Number.isFinite(promptCount) ? promptCount : 5));

    setIsGenerating(true);
    setGenProgress(null);

    const result = await promptApi.generate(
      batchId!,
      [expandedType],
      selectedImageId || undefined,
      normalizedPromptCount,
      true,
    );
    if (!result) {
      setIsGenerating(false);
      return;
    }

    const typeName = allTypes.find((t) => t.id === expandedType)?.name || expandedType;
    const selectedImage = images.find((img) => img.id === selectedImageId);
    toast.info("提示词生成已启动", {
      description: `正在为「${typeName}」生成 ${normalizedPromptCount} 条提示词（参考底图：${selectedImage?.filename || "默认首图"}）...`,
    });

    const finalInfo = await pollGenerateProgress(batchId!);
    setIsGenerating(false);

    if (finalInfo?.status === "completed") {
      toast.success(`「${typeName}」提示词生成完成！`);
      await loadPrompts();
    } else if (finalInfo?.status === "cancelled") {
      toast.info(`「${typeName}」提示词生成已中断`);
      await loadPrompts();
    } else {
      toast.error("提示词生成失败或超时");
    }
  }, [batchId, expandedType, selectedImageId, pollGenerateProgress, loadPrompts, promptCount]);

  // ========== 为单个人群类型重新生成 ==========

  const handleRegenerate = useCallback(async (typeId: string) => {
    if (!batchId) {
      toast.info("演示模式：无法调用后端生成");
      return;
    }

    if (!crowdTypes.single.some((t) => t.id === typeId)) {
      toast.info("当前版本仅支持单人7类，组合人群暂未开放");
      return;
    }

    const normalizedPromptCount = Math.max(1, Math.min(20, Number.isFinite(promptCount) ? promptCount : 5));

    setIsGenerating(true);
    setGenProgress(null);

    const result = await promptApi.generate(
      batchId!,
      [typeId],
      selectedImageId || undefined,
      normalizedPromptCount,
      true,
    );
    if (!result) {
      setIsGenerating(false);
      return;
    }

    const typeName = allTypes.find((t) => t.id === typeId)?.name || typeId;
    toast.info(`正在为「${typeName}」重新生成 ${normalizedPromptCount} 条提示词...`);

    const finalInfo = await pollGenerateProgress(batchId!);
    setIsGenerating(false);

    if (finalInfo?.status === "completed") {
      toast.success(`「${typeName}」提示词已重新生成`);
      await loadPrompts();
    } else if (finalInfo?.status === "cancelled") {
      toast.info(`「${typeName}」提示词生成已中断`);
      await loadPrompts();
    } else {
      toast.error("重新生成失败或超时");
    }
  }, [batchId, selectedImageId, pollGenerateProgress, loadPrompts, promptCount]);

  const handleCancelGenerate = useCallback(async () => {
    if (!batchId) return;
    const result = await promptApi.cancel(batchId);
    if (result !== undefined) {
      toast.info("已发送中断请求，任务将在安全点停止");
    }
  }, [batchId]);

  const savePromptDraft = useCallback(async (promptId: string) => {
    const draft = pendingDraftRef.current[promptId];
    if (!draft || Object.keys(draft).length === 0) {
      return true;
    }
    const result = await promptApi.edit(promptId, draft);
    if (result === undefined) {
      return false;
    }
    delete pendingDraftRef.current[promptId];
    return true;
  }, []);

  const flushPendingSaves = useCallback(async () => {
    const ids = Object.keys(pendingDraftRef.current);
    if (ids.length === 0) return true;

    for (const id of ids) {
      if (saveTimerRef.current[id]) {
        clearTimeout(saveTimerRef.current[id]);
        delete saveTimerRef.current[id];
      }
    }

    const results = await Promise.all(ids.map((id) => savePromptDraft(id)));
    return results.every(Boolean);
  }, [savePromptDraft]);

  // ========== 编辑提示词（防抖自动保存） ==========

  const handleUpdatePrompt = useCallback(
    (promptId: string, field: "positive_prompt" | "negative_prompt" | "style_name", value: string) => {
      // 立即更新本地状态
      setPromptMap((prev) => {
        const next = { ...prev };
        for (const key of Object.keys(next)) {
          next[key] = next[key].map((p) =>
            p.id === promptId ? { ...p, [field]: value } : p
          );
        }
        return next;
      });

      pendingDraftRef.current[promptId] = {
        ...pendingDraftRef.current[promptId],
        [field]: value,
      };

      if (saveTimerRef.current[promptId]) {
        clearTimeout(saveTimerRef.current[promptId]);
      }
      saveTimerRef.current[promptId] = setTimeout(async () => {
        await savePromptDraft(promptId);
        delete saveTimerRef.current[promptId];
      }, 800);
    },
    [savePromptDraft],
  );

  // ========== 手动保存（清空防抖队列） ==========

  const handleSave = useCallback(async () => {
    const success = await flushPendingSaves();
    if (success) {
      toast.success("保存成功", { description: "提示词词库已同步到后端" });
    } else {
      toast.error("保存失败，请重试");
    }
  }, [flushPendingSaves]);

  // ========== 删除提示词 ==========

  const handleDeletePrompt = useCallback(async (typeId: string, promptId: string) => {
    if (saveTimerRef.current[promptId]) {
      clearTimeout(saveTimerRef.current[promptId]);
      delete saveTimerRef.current[promptId];
    }
    delete pendingDraftRef.current[promptId];
    await promptApi.delete(promptId);
    setPromptMap((prev) => ({
      ...prev,
      [typeId]: (prev[typeId] || []).filter((p) => p.id !== promptId),
    }));
    toast.info("已删除提示词");
  }, []);

  const handleDeleteTypePrompts = useCallback(async () => {
    if (!expandedType) {
      toast.info("请先选择人群类型");
      return;
    }

    const prompts = promptMap[expandedType] || [];
    if (prompts.length === 0) {
      toast.info("当前类型暂无可删除的提示词");
      return;
    }

    if (!window.confirm(`确定删除当前类型的 ${prompts.length} 条提示词吗？此操作不可撤销。`)) {
      return;
    }

    await promptApi.deleteByCrowd(expandedType);
    setPromptMap((prev) => ({ ...prev, [expandedType]: [] }));
    const typeName = allTypes.find((t) => t.id === expandedType)?.name || expandedType;
    toast.success(`已清空「${typeName}」提示词`);
  }, [expandedType, promptMap]);

  // ========== 图片选择 ==========

  const handleImageSelect = useCallback((imageId: string) => {
    setSelectedImageId(imageId);
    const img = images.find((i) => i.id === imageId);
    if (img) {
      toast.info("已切换底图", { description: `当前选中: ${img.filename}` });
    }
  }, [images]);

  const handleExportLibrary = useCallback(async () => {
    setIsExportingLibrary(true);
    const toastId = toast.loading("正在导出整个提示词库...");
    try {
      const saved = await flushPendingSaves();
      if (!saved) {
        toast.dismiss(toastId);
        toast.error("导出前自动保存失败，请先重试保存");
        return;
      }

      const payload = await promptApi.exportLibrary();
      if (!payload) {
        toast.dismiss(toastId);
        return;
      }

      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json;charset=utf-8",
      });
      const downloadUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = downloadUrl;
      link.download = buildPromptLibraryFilename(payload.exported_at);
      link.click();
      URL.revokeObjectURL(downloadUrl);

      toast.dismiss(toastId);
      toast.success("整库导出成功", {
        description: `已导出 ${payload.summary?.prompt_count ?? payload.prompts.length} 条提示词`,
      });
    } catch {
      toast.dismiss(toastId);
      toast.error("导出失败，请重试");
    } finally {
      setIsExportingLibrary(false);
    }
  }, [flushPendingSaves]);

  const handleOpenBackupDialog = useCallback(() => {
    setIsBackupDialogOpen(true);
  }, []);

  const handlePickBackupFile = useCallback(() => {
    backupImportFileRef.current?.click();
  }, []);

  const handleBackupFileSelected = useCallback(async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    setIsImportingLibrary(true);
    const toastId = toast.loading("正在解析词库文件...");
    try {
      const raw = await file.text();
      const parsed = JSON.parse(raw);
      const library = normalizeImportedPromptLibrary(parsed);
      if (!library) {
        toast.dismiss(toastId);
        toast.error("文件格式不正确，必须是提示词库 JSON 或完整备份 JSON");
        return;
      }
      toast.dismiss(toastId);
      setSelectedImportFileName(file.name);
      setPendingImportLibrary(library);
      setIsBackupDialogOpen(true);
      toast.success("备份文件已载入", {
        description: `检测到 ${library.summary?.prompt_count ?? library.prompts.length} 条提示词`,
      });
    } catch {
      toast.dismiss(toastId);
      toast.error("导入失败，请检查 JSON 内容");
    } finally {
      setIsImportingLibrary(false);
      event.target.value = "";
    }
  }, []);

  const handleOpenFlexibleImportDialog = useCallback(() => {
    if (!expandedType) {
      toast.info("请先选择一个人群类型");
      return;
    }
    const typeName = allTypes.find((type) => type.id === expandedType)?.name || "人物";
    setReplaceCurrentType(false);
    setFlexibleImportFileName("");
    if (!flexibleImportText.trim()) {
      setFlexibleImportText(
        `模板一 | 严格参考原图背景和光影，仅替换${typeName}主体；服饰：新中式套装；发型：低盘发；动作：扶栏微笑；景别：半身；站位：右侧三分之一 | 背景替换,地标变更,多人,遮挡脸\n模板二 | 严格参考原图背景和光影，仅替换${typeName}主体；服饰：都市通勤西装；发型：低马尾；动作：自然前行；景别：全身；站位：前景偏左 | 背景替换,地标变更,多人,遮挡脸`,
      );
    }
    setIsFlexibleImportDialogOpen(true);
  }, [expandedType, flexibleImportText]);

  const handlePickFlexibleImportFile = useCallback(() => {
    flexibleImportFileRef.current?.click();
  }, []);

  const handleFlexibleImportFileSelected = useCallback(async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    const toastId = toast.loading("正在读取灵活导入文件...");
    try {
      const raw = await file.text();
      try {
        const parsed = JSON.parse(raw);
        const backupLibrary = normalizeImportedPromptLibrary(parsed);
        if (backupLibrary) {
          setSelectedImportFileName(file.name);
          setPendingImportLibrary(backupLibrary);
          setIsFlexibleImportDialogOpen(false);
          setIsBackupDialogOpen(true);
          toast.dismiss(toastId);
          toast.info("检测到整库备份文件，已切换到「备份还原」");
          return;
        }
      } catch {
        // 非 JSON 继续按灵活导入文本处理
      }
      setFlexibleImportText(raw);
      setFlexibleImportFileName(file.name);
      toast.dismiss(toastId);
      toast.success("灵活导入文件已载入", {
        description: "你可以直接预览并一键写入当前类型",
      });
    } catch {
      toast.dismiss(toastId);
      toast.error("文件读取失败，请重试");
    } finally {
      event.target.value = "";
    }
  }, []);

  const handleFillPipeExample = useCallback(() => {
    if (!expandedType) return;
    const typeName = allTypes.find((type) => type.id === expandedType)?.name || "人物";
    setFlexibleImportText(
      `模板一 | 严格参考原图背景和光影，仅替换${typeName}主体；服饰：新中式套装；发型：低盘发；动作：扶栏微笑；景别：半身；站位：右侧三分之一 | 背景替换,地标变更,多人,遮挡脸\n模板二 | 严格参考原图背景和光影，仅替换${typeName}主体；服饰：都市通勤西装；发型：低马尾；动作：自然前行；景别：全身；站位：前景偏左 | 背景替换,地标变更,多人,遮挡脸`,
    );
    toast.info("已填入竖线语法示例");
  }, [expandedType]);

  const handleFillJsonExample = useCallback(() => {
    if (!expandedType) return;
    const typeName = allTypes.find((type) => type.id === expandedType)?.name || "人物";
    setFlexibleImportText(JSON.stringify({
      模板一: {
        positive_prompt: `严格参考原图背景和光影，仅替换${typeName}主体；服饰：新中式套装；发型：低盘发；动作：扶栏微笑；景别：半身；站位：右侧三分之一`,
        negative_prompt: "背景替换,地标变更,多人,遮挡脸",
      },
      模板二: {
        positive_prompt: `严格参考原图背景和光影，仅替换${typeName}主体；服饰：都市通勤西装；发型：低马尾；动作：自然前行；景别：全身；站位：前景偏左`,
        negative_prompt: "背景替换,地标变更,多人,遮挡脸",
      },
    }, null, 2));
    toast.info("已填入 JSON 示例");
  }, [expandedType]);

  const handleSubmitFlexibleImport = useCallback(async () => {
    if (!expandedType) {
      toast.info("请先选择一个人群类型");
      return;
    }

    const typeName = allTypes.find((type) => type.id === expandedType)?.name || "模板";
    const parsed = parseFlexibleImportText(flexibleImportText, `${typeName}模板`);
    if (parsed.items.length === 0) {
      toast.error(parsed.errors[0] || "未识别到可导入的内容");
      return;
    }

    const toastId = toast.loading("正在写入灵活导入内容...");
    setIsFlexibleImportSubmitting(true);
    try {
      const saved = await flushPendingSaves();
      if (!saved) {
        toast.dismiss(toastId);
        toast.error("导入前自动保存失败，请先重试保存");
        return;
      }

      const result = await promptApi.bulkUpsert(expandedType, parsed.items, replaceCurrentType);
      if (!result) {
        toast.dismiss(toastId);
        return;
      }

      await loadPrompts();
      toast.dismiss(toastId);
      setIsFlexibleImportDialogOpen(false);
      toast.success("灵活导入完成", {
        description: `已写入 ${result.total} 条模板（新增 ${result.created_count}，更新 ${result.updated_count}）`,
      });
      if (parsed.errors.length > 0) {
        toast.warning(`解析时有 ${parsed.errors.length} 条告警，已忽略异常行`);
      }
    } catch {
      toast.dismiss(toastId);
      toast.error("灵活导入失败，请重试");
    } finally {
      setIsFlexibleImportSubmitting(false);
    }
  }, [expandedType, flexibleImportText, flushPendingSaves, loadPrompts, replaceCurrentType]);

  const handleCreateTasksFromLibrary = useCallback(async () => {
    if (!batchId) {
      toast.info("请先上传并完成预处理底图，再按词库创建任务");
      return;
    }

    const activeCrowdTypes = Object.entries(promptMap)
      .filter(([, prompts]) => prompts.length > 0)
      .map(([crowdType]) => crowdType);
    if (activeCrowdTypes.length === 0) {
      toast.info("当前词库为空，请先导入或生成提示词");
      return;
    }

    const confirmCreate = window.confirm(
      "将按当前整个提示词库重建当前批次的待生图任务，并清理该批次已有任务，确定继续吗？",
    );
    if (!confirmCreate) return;

    setIsCreatingTasks(true);
    const toastId = toast.loading("正在按当前词库创建任务...");
    try {
      const saved = await flushPendingSaves();
      if (!saved) {
        toast.dismiss(toastId);
        toast.error("创建任务前自动保存失败，请先重试保存");
        return;
      }

      const result = await promptApi.createTasks(batchId, activeCrowdTypes, true);
      if (!result) {
        toast.dismiss(toastId);
        return;
      }

      await loadPrompts();
      toast.dismiss(toastId);
      toast.success("任务创建完成", {
        description: `${result.base_image_count} 张底图 × ${result.template_count} 条词库模板，已创建 ${result.pending_count} 个待处理任务`,
      });
    } catch {
      toast.dismiss(toastId);
      toast.error("创建任务失败，请重试");
    } finally {
      setIsCreatingTasks(false);
    }
  }, [batchId, flushPendingSaves, loadPrompts, promptMap]);

  const handleConfirmImportBackup = useCallback(async () => {
    if (!pendingImportLibrary) {
      toast.info("请先选择备份文件");
      return;
    }

    setIsImportingLibrary(true);
    const toastId = toast.loading("正在导入备份...");
    try {
      const saved = await flushPendingSaves();
      if (!saved) {
        toast.dismiss(toastId);
        toast.error("导入前自动保存失败，请先重试保存");
        return;
      }

      const result = await promptApi.importLibrary(pendingImportLibrary, true);
      if (!result) {
        toast.dismiss(toastId);
        return;
      }

      await loadPrompts();
      toast.dismiss(toastId);
      setIsBackupDialogOpen(false);
      toast.success("备份导入成功", {
        description: `已写入 ${result.prompt_count} 条提示词（新建 ${result.created_count}，更新 ${result.updated_count}）`,
      });
    } catch {
      toast.dismiss(toastId);
      toast.error("备份导入失败，请重试");
    } finally {
      setIsImportingLibrary(false);
    }
  }, [flushPendingSaves, loadPrompts, pendingImportLibrary]);

  // ========== 辅助 ==========

  const getTypePromptCount = (typeId: string) => promptMap[typeId]?.length || 0;
  const currentPrompts = expandedType ? (promptMap[expandedType] || []) : [];
  const currentTypeName = allTypes.find((type) => type.id === expandedType)?.name || "模板";
  const totalPromptCount = Object.values(promptMap).reduce((sum, prompts) => sum + prompts.length, 0);
  const flexibleImportPreview = parseFlexibleImportText(flexibleImportText, `${currentTypeName}模板`);

  // ========== 渲染 ==========

  return (
    <MainLayout
      title="提示词"
      actions={
        <div className="flex flex-wrap items-center gap-3">
          {isGenerating && genProgress && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>
                {genProgress.progress}% ({genProgress.completed}/{genProgress.total})
              </span>
            </div>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={handleOpenFlexibleImportDialog}
            disabled={isFlexibleImportSubmitting || isImportingLibrary || isExportingLibrary || isCreatingTasks}
          >
            {isFlexibleImportSubmitting ? (
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
            ) : (
              <ClipboardPaste className="w-4 h-4 mr-2" />
            )}
            {isFlexibleImportSubmitting ? "写入中..." : "灵活导入"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleOpenBackupDialog}
            disabled={isFlexibleImportSubmitting || isImportingLibrary || isExportingLibrary || isCreatingTasks}
          >
            <FileText className="w-4 h-4 mr-2" />
            备份还原
          </Button>
          <Button variant="outline" size="sm" onClick={handleSave}>
            <Save className="w-4 h-4 mr-2" />
            保存配置
          </Button>
          {isGenerating && (
            <Button variant="destructive" size="sm" onClick={handleCancelGenerate}>
              <Pause className="w-4 h-4 mr-2" />
              中断
            </Button>
          )}
          <Button
            size="sm"
            onClick={handleCreateTasksFromLibrary}
            disabled={isCreatingTasks || isImportingLibrary || isExportingLibrary}
          >
            {isCreatingTasks ? (
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
            ) : (
              <Sparkles className="w-4 h-4 mr-2" />
            )}
            {isCreatingTasks ? "创建中..." : "按当前词库创建任务"}
          </Button>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">本次使用条数</span>
            <Input
              type="number"
              min={1}
              max={20}
              value={promptCount}
              onChange={(e) => {
                const val = Number(e.target.value || 5);
                if (!Number.isFinite(val)) return;
                setPromptCount(Math.max(1, Math.min(20, Math.round(val))));
              }}
              className="h-8 w-20"
              disabled={isGenerating || isCreatingTasks}
            />
          </div>
        </div>
      }
    >
      <div className="flex gap-4 h-[calc(100vh-140px)]">
        {/* 最左侧：预处理后的图片列表 */}
        <Card className="w-[140px] shrink-0 flex flex-col">
          <CardHeader className="py-2 px-3 shrink-0">
            <CardTitle className="text-base flex items-center gap-1">
              <ImageIcon className="w-4 h-4" />
              底图列表
            </CardTitle>
          </CardHeader>
          <CardContent className="p-2 pt-0 flex-1 overflow-hidden">
            <ScrollArea className="h-full">
              {loadingImages ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
                </div>
              ) : (
                <div className="space-y-2">
                  {images.map((image, index) => (
                    <div
                      key={image.id}
                      className={cn(
                        "relative cursor-pointer rounded-lg overflow-hidden transition-all duration-200",
                        selectedImageId === image.id
                          ? "ring-2 ring-primary ring-offset-2"
                          : "hover:ring-2 hover:ring-muted-foreground/30"
                      )}
                      onClick={() => handleImageSelect(image.id)}
                    >
                      <div className="aspect-[2/3] relative bg-muted">
                        <img
                          src={image.thumbnail}
                          alt={image.filename}
                          className="absolute inset-0 w-full h-full object-cover"
                        />
                        <div className="absolute top-1 left-1 bg-black/60 text-white text-xs px-1.5 py-0.5 rounded">
                          {index + 1}
                        </div>
                      </div>
                    </div>
                  ))}
                  {images.length === 0 && (
                    <p className="text-xs text-muted-foreground text-center py-4">
                      暂无底图
                    </p>
                  )}
                </div>
              )}
            </ScrollArea>
          </CardContent>
        </Card>

        {/* 中间：人群类型选择 */}
        <Card className="w-[280px] shrink-0 flex flex-col">
          <CardHeader className="py-2 shrink-0">
            <CardTitle className="text-base">人群类型</CardTitle>
          </CardHeader>
          <CardContent className="p-0 flex-1 overflow-hidden">
            <ScrollArea className="h-full">
              <div className="px-4 pb-4">
                <div className="mb-4">
                  <h4 className="text-sm font-medium text-muted-foreground mb-2 px-2">
                    单人类型（7种）
                  </h4>
                  <div className="space-y-1">
                    {crowdTypes.single.map((type) => (
                      <div
                        key={type.id}
                        className={cn(
                          "flex items-center gap-3 px-3 py-2 rounded-lg cursor-pointer transition-colors",
                          expandedType === type.id ? "bg-accent" : "hover:bg-muted"
                        )}
                        onClick={() => setExpandedType(expandedType === type.id ? null : type.id)}
                      >
                        <div className="flex-1">
                          <div className="flex items-center justify-between">
                            <span className="text-sm font-medium">{type.name}</span>
                            <span className="text-xs text-muted-foreground">
                              {getTypePromptCount(type.id)} 个
                            </span>
                          </div>
                        </div>
                        {expandedType === type.id ? (
                          <ChevronDown className="w-4 h-4 text-muted-foreground" />
                        ) : (
                          <ChevronRight className="w-4 h-4 text-muted-foreground" />
                        )}
                      </div>
                    ))}
                  </div>
                </div>

                <div className="border-t border-border my-4" />
                <p className="text-xs text-muted-foreground px-2">
                  组合类型（12种）将在后续版本开放，当前仅支持单人7类。
                </p>
              </div>
            </ScrollArea>
          </CardContent>
        </Card>

        {/* 右侧：提示词编辑区 */}
        <Card className="flex-1 flex flex-col">
          <CardHeader className="py-2 shrink-0">
            <div className="flex items-center justify-between">
              <CardTitle className="text-base">
                {expandedType
                  ? `${allTypes.find((t) => t.id === expandedType)?.name} - 提示词列表`
                  : "请选择人群类型"}
              </CardTitle>
              {expandedType && (
                <div className="flex gap-2">
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={handleDeleteTypePrompts}
                    disabled={isGenerating || currentPrompts.length === 0}
                  >
                    <Trash2 className="w-4 h-4 mr-2" />
                    清空当前类型
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleRegenerate(expandedType)}
                    disabled={isGenerating}
                  >
                    {isGenerating ? (
                      <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    ) : (
                      <RefreshCw className="w-4 h-4 mr-2" />
                    )}
                    AI重新生成
                  </Button>
                </div>
              )}
            </div>
          </CardHeader>
          <CardContent className="flex-1 overflow-hidden">
            {loadingPrompts ? (
              <div className="flex items-center justify-center h-full">
                <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
              </div>
            ) : expandedType ? (
              <ScrollArea className="h-full">
                <div className="space-y-4 pr-4">
                  {currentPrompts.map((prompt, index) => (
                    <Card key={prompt.id} className="border border-border">
                      <CardContent className="p-4">
                        <div className="flex items-start justify-between mb-3">
                          <div className="flex items-center gap-2">
                            <span className="w-6 h-6 rounded-full bg-primary/10 text-primary text-xs flex items-center justify-center font-medium">
                              {index + 1}
                            </span>
                            {editingId === prompt.id ? (
                              <Input
                                value={prompt.style_name}
                                onChange={(e) =>
                                  handleUpdatePrompt(prompt.id, "style_name", e.target.value)
                                }
                                className="h-8 w-40"
                                autoFocus
                                onBlur={() => setEditingId(null)}
                                onKeyDown={(e) => e.key === "Enter" && setEditingId(null)}
                              />
                            ) : (
                              <span className="font-medium">{prompt.style_name}</span>
                            )}
                            {prompt.task_count > 0 && (
                              <span className="text-xs bg-muted px-1.5 py-0.5 rounded text-muted-foreground">
                                {prompt.task_count} 任务
                              </span>
                            )}
                          </div>
                          <div className="flex items-center gap-1">
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8"
                              onClick={() => setEditingId(prompt.id)}
                              title="编辑风格名称"
                            >
                              <Edit3 className="w-4 h-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8 text-destructive hover:text-destructive"
                              onClick={() => handleDeletePrompt(expandedType, prompt.id)}
                              title="删除提示词"
                            >
                              <Trash2 className="w-4 h-4" />
                            </Button>
                          </div>
                        </div>

                        {/* 正向提示词 */}
                        <div className="mb-2">
                          <label className="text-xs text-muted-foreground mb-1 block">
                            正向提示词 (Positive)
                          </label>
                          <Textarea
                            value={prompt.positive_prompt}
                            onChange={(e) =>
                              handleUpdatePrompt(prompt.id, "positive_prompt", e.target.value)
                            }
                            rows={3}
                            className="resize-none text-sm"
                            placeholder="输入正向提示词..."
                          />
                        </div>

                        {/* 负向提示词 */}
                        <div className="mb-2">
                          <label className="text-xs text-muted-foreground mb-1 block">
                            负向提示词 (Negative)
                          </label>
                          <Textarea
                            value={prompt.negative_prompt || ""}
                            onChange={(e) =>
                              handleUpdatePrompt(prompt.id, "negative_prompt", e.target.value)
                            }
                            rows={2}
                            className="resize-none text-sm"
                            placeholder="输入负向提示词..."
                          />
                        </div>

                        {/* 参考权重 & 引擎 */}
                        <div className="flex items-center gap-4 text-xs text-muted-foreground">
                          <span>参考权重: {prompt.reference_weight}</span>
                          <span>引擎: {prompt.preferred_engine || "默认"}</span>
                        </div>
                      </CardContent>
                    </Card>
                  ))}

                  {currentPrompts.length === 0 && (
                    <div className="text-center py-12">
                      <p className="text-muted-foreground mb-4">
                        {batchId
                          ? "暂无提示词，请先灵活导入、备份还原，或为当前类型生成提示词"
                          : "暂无提示词，请先灵活导入或通过备份还原恢复"}
                      </p>
                      <div className="flex flex-wrap items-center justify-center gap-2">
                        <Button variant="outline" onClick={handleOpenFlexibleImportDialog}>
                          <ClipboardPaste className="w-4 h-4 mr-2" />
                          灵活导入
                        </Button>
                        <Button variant="outline" onClick={handleOpenBackupDialog}>
                          <FileText className="w-4 h-4 mr-2" />
                          备份还原
                        </Button>
                        {batchId && (
                          <Button
                            variant="outline"
                            onClick={() => handleRegenerate(expandedType)}
                            disabled={isGenerating}
                          >
                            <Sparkles className="w-4 h-4 mr-2" />
                            为此类型生成提示词
                          </Button>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              </ScrollArea>
            ) : (
              <div className="flex items-center justify-center h-[calc(100vh-320px)]">
                <p className="text-muted-foreground">请从左侧选择一个人群类型查看提示词</p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <input
        ref={backupImportFileRef}
        type="file"
        accept=".json,application/json"
        className="hidden"
        onChange={handleBackupFileSelected}
      />

      <input
        ref={flexibleImportFileRef}
        type="file"
        accept=".txt,.md,.json,text/plain,application/json"
        className="hidden"
        onChange={handleFlexibleImportFileSelected}
      />

      <Dialog
        open={isBackupDialogOpen}
        onOpenChange={(open) => {
          if (!isImportingLibrary && !isExportingLibrary) {
            setIsBackupDialogOpen(open);
          }
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>备份还原</DialogTitle>
            <DialogDescription>
              这里处理的是整库提示词备份。单人物模板的 `|` / JSON 灵活导入，请使用上方的「灵活导入」。
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-4 md:grid-cols-2">
            <div className="rounded-xl border p-4 space-y-3">
              <div className="flex items-center gap-2 text-sm font-medium">
                <Download className="w-4 h-4" />
                导出整库备份
              </div>
              <p className="text-sm text-muted-foreground leading-6">
                导出当前全部提示词模板，适合跨环境迁移或长期留档。
              </p>
              <div className="text-2xl font-semibold">{totalPromptCount}</div>
              <div className="text-xs text-muted-foreground">当前整库模板总数</div>
              <Button
                variant="outline"
                className="w-full"
                onClick={handleExportLibrary}
                disabled={isExportingLibrary || isImportingLibrary}
              >
                {isExportingLibrary ? (
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                ) : (
                  <Download className="w-4 h-4 mr-2" />
                )}
                {isExportingLibrary ? "导出中..." : "导出备份"}
              </Button>
            </div>

            <div className="rounded-xl border p-4 space-y-3">
              <div className="flex items-center gap-2 text-sm font-medium">
                <Upload className="w-4 h-4" />
                导入整库备份
              </div>
              <p className="text-sm text-muted-foreground leading-6">
                支持 `prompt-library` JSON 和完整备份 JSON。导入会替换当前整个提示词库。
              </p>
              <div className="rounded-lg bg-muted/40 px-3 py-2 text-sm break-all min-h-10">
                {selectedImportFileName || "尚未选择备份文件"}
              </div>
              <Button
                variant="outline"
                className="w-full"
                onClick={handlePickBackupFile}
                disabled={isImportingLibrary || isExportingLibrary}
              >
                {isImportingLibrary ? (
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                ) : (
                  <Upload className="w-4 h-4 mr-2" />
                )}
                {isImportingLibrary ? "读取中..." : "选择备份文件"}
              </Button>
            </div>
          </div>

          {pendingImportLibrary && (
            <div className="rounded-xl border p-4 space-y-4">
              <div className="grid gap-3 md:grid-cols-2">
                <div>
                  <div className="text-xs text-muted-foreground mb-1">即将还原</div>
                  <div className="text-sm font-medium">{pendingImportLibrary.app_name}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground mb-1">提示词条数</div>
                  <div className="text-sm font-medium">
                    {pendingImportLibrary.summary?.prompt_count ?? pendingImportLibrary.prompts.length}
                  </div>
                </div>
              </div>
              <div className="text-sm text-muted-foreground leading-6">
                为避免和单类型灵活导入混淆，这里会直接覆盖整库。确认前建议先导出一次当前词库。
              </div>
              <DialogFooter className="gap-2">
                <Button
                  variant="outline"
                  onClick={handlePickBackupFile}
                  disabled={isImportingLibrary}
                >
                  重新选择文件
                </Button>
                <Button
                  onClick={handleConfirmImportBackup}
                  disabled={!pendingImportLibrary || isImportingLibrary}
                >
                  {isImportingLibrary ? (
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  ) : (
                    <Upload className="w-4 h-4 mr-2" />
                  )}
                  {isImportingLibrary ? "导入中..." : "确认导入备份"}
                </Button>
              </DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>

      <Dialog
        open={isFlexibleImportDialogOpen}
        onOpenChange={(open) => {
          if (!isFlexibleImportSubmitting) {
            setIsFlexibleImportDialogOpen(open);
          }
        }}
      >
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>灵活导入</DialogTitle>
            <DialogDescription>
              当前写入目标：{expandedType ? `${currentTypeName} (${expandedType})` : "未选择人群类型"}。支持 `模板名 | 正向提示词 | 负向提示词`、Markdown 表格和 JSON 单人物模板。
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="outline" size="sm" onClick={handleFillPipeExample}>
                填入竖线示例
              </Button>
              <Button variant="outline" size="sm" onClick={handleFillJsonExample}>
                填入 JSON 示例
              </Button>
              <Button variant="outline" size="sm" onClick={handlePickFlexibleImportFile}>
                <Upload className="w-4 h-4 mr-2" />
                读取本地文本/JSON
              </Button>
              <span className="text-xs text-muted-foreground">
                {flexibleImportFileName ? `当前文件：${flexibleImportFileName}` : "也可以直接粘贴 AI 生成的 `|` 语法内容"}
              </span>
            </div>

            <Textarea
              value={flexibleImportText}
              onChange={(event) => setFlexibleImportText(event.target.value)}
              rows={14}
              className="font-mono text-xs"
              placeholder={"模板一 | 正向提示词 | 负向提示词\n模板二 | 正向提示词 | 负向提示词"}
            />

            <div className="grid gap-3 md:grid-cols-2">
              <div className="rounded-lg border p-4">
                <div className="text-sm font-medium">预解析结果</div>
                <div className="mt-2 text-2xl font-semibold">{flexibleImportPreview.items.length}</div>
                <div className="text-xs text-muted-foreground">可写入模板数</div>
              </div>
              <div className="rounded-lg border p-4">
                <div className="text-sm font-medium">解析告警</div>
                <div className="mt-2 text-2xl font-semibold">{flexibleImportPreview.errors.length}</div>
                <div className="text-xs text-muted-foreground">异常行会被自动忽略</div>
              </div>
            </div>

            <div className="rounded-lg border p-4 space-y-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-medium">覆盖当前类型</div>
                  <div className="text-xs text-muted-foreground">
                    开启后会先停用当前类型已有词条，再写入本次灵活导入内容。
                  </div>
                </div>
                <Switch checked={replaceCurrentType} onCheckedChange={setReplaceCurrentType} />
              </div>
              {flexibleImportPreview.errors.length > 0 && (
                <div className="text-xs text-muted-foreground leading-6">
                  {flexibleImportPreview.errors.slice(0, 3).join("；")}
                </div>
              )}
            </div>
          </div>

          <DialogFooter className="gap-2">
            <Button
              variant="outline"
              onClick={() => setIsFlexibleImportDialogOpen(false)}
              disabled={isFlexibleImportSubmitting}
            >
              取消
            </Button>
            <Button
              onClick={handleSubmitFlexibleImport}
              disabled={!expandedType || isFlexibleImportSubmitting}
            >
              {isFlexibleImportSubmitting ? (
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              ) : (
                <ClipboardPaste className="w-4 h-4 mr-2" />
              )}
              {isFlexibleImportSubmitting ? "写入中..." : "写入当前类型"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 生成进度浮层 */}
      {isGenerating && genProgress && (
        <div className="fixed bottom-6 right-6 w-80 bg-popover border rounded-lg shadow-lg p-4 z-50">
          <div className="flex items-center gap-2 mb-2">
            <Loader2 className="w-4 h-4 animate-spin" />
            <span className="text-sm font-medium">提示词生成中...</span>
          </div>
          <div className="w-full bg-muted rounded-full h-2 mb-2">
            <div
              className="bg-primary h-2 rounded-full transition-all duration-300"
              style={{ width: `${genProgress.progress}%` }}
            />
          </div>
          <div className="text-xs text-muted-foreground">
            进度 {genProgress.progress}% · 完成 {genProgress.completed} · 失败 {genProgress.failed}
          </div>
        </div>
      )}
    </MainLayout>
  );
}
