import { useState, useEffect, useCallback, useRef, type ChangeEvent } from "react";
import { MainLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";
import {
  Wand2,
  Save,
  ChevronRight,
  ChevronDown,
  Trash2,
  Edit3,
  ImageIcon,
  Loader2,
  Pause,
  Plus,
  Upload,
  Download,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useUpload } from "@/contexts/UploadContext";
import {
  uploadApi,
  promptApi,
  toFileUrl,
  type PromptItem,
  type ProgressInfo,
} from "@/lib/api";

interface BaseImageInfo {
  id: string;
  filename: string;
  thumbnail: string;
}

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

function groupByCrowdType(prompts: PromptItem[]): Record<string, PromptItem[]> {
  const map: Record<string, PromptItem[]> = {};
  for (const p of prompts) {
    (map[p.crowd_type] ??= []).push(p);
  }
  return map;
}

export default function PromptConfig() {
  const { batchId } = useUpload();

  const [images, setImages] = useState<BaseImageInfo[]>([]);
  const [selectedImageId, setSelectedImageId] = useState<string>("");

  const [promptMap, setPromptMap] = useState<Record<string, PromptItem[]>>({});
  const [expandedType, setExpandedType] = useState<string | null>("C02");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [promptCount, setPromptCount] = useState(5);

  const [isGenerating, setIsGenerating] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [genProgress, setGenProgress] = useState<ProgressInfo | null>(null);

  const [loadingImages, setLoadingImages] = useState(false);
  const [loadingPrompts, setLoadingPrompts] = useState(false);

  const saveTimerRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const importInputRef = useRef<HTMLInputElement | null>(null);

  const loadPrompts = useCallback(async () => {
    if (!batchId) {
      setPromptMap({});
      return;
    }
    setLoadingPrompts(true);
    const result = await promptApi.list({ batch_id: batchId });
    if (result) {
      setPromptMap(groupByCrowdType(result.prompts));
    }
    setLoadingPrompts(false);
  }, [batchId]);

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
          }));
        setImages(completed);
        if (completed.length > 0) {
          setSelectedImageId((prev) => prev || completed[0].id);
        }
      }
      setLoadingImages(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [batchId]);

  useEffect(() => {
    loadPrompts();
  }, [loadPrompts]);

  const pollGenerateProgress = useCallback(async (bid: string) => {
    const POLL_INTERVAL = 2000;
    const MAX_POLLS = 180;
    let polls = 0;

    while (polls < MAX_POLLS) {
      await new Promise((r) => setTimeout(r, POLL_INTERVAL));
      polls++;
      const info = await promptApi.progress(bid);
      if (!info) return null;
      setGenProgress(info);
      if (info.status === "completed" || info.status === "error" || info.status === "cancelled") {
        return info;
      }
    }
    return null;
  }, []);

  useEffect(() => {
    if (!batchId) return;
    let cancelled = false;
    (async () => {
      const info = await promptApi.progress(batchId);
      if (cancelled || !info) return;
      if (info.status === "running" || info.status === "cancelling") {
        setIsGenerating(true);
        setGenProgress(info);
        const finalInfo = await pollGenerateProgress(batchId);
        if (cancelled) return;
        setIsGenerating(false);
        if (finalInfo?.status === "completed") {
          await loadPrompts();
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [batchId, pollGenerateProgress, loadPrompts]);

  const handleApplyCurrentType = useCallback(async () => {
    if (!batchId) {
      toast.info("请先完成素材上传");
      return;
    }
    if (!expandedType) {
      toast.info("请先选择人群类型");
      return;
    }
    if (!crowdTypes.single.some((t) => t.id === expandedType)) {
      toast.info("当前版本仅支持单人7类");
      return;
    }

    const currentPrompts = promptMap[expandedType] || [];
    if (currentPrompts.length === 0) {
      toast.error("当前类型没有可用提示词，请先新增或导入");
      return;
    }

    const normalizedPromptCount = Math.max(1, Math.min(20, Number(promptCount) || 5));
    setIsGenerating(true);
    setGenProgress(null);

    const result = await promptApi.generate(
      batchId,
      [expandedType],
      selectedImageId || undefined,
      normalizedPromptCount,
    );
    if (!result) {
      setIsGenerating(false);
      return;
    }

    const typeName = allTypes.find((t) => t.id === expandedType)?.name || expandedType;
    toast.info("已开始创建生图任务", {
      description: `类型「${typeName}」，本次使用 ${normalizedPromptCount} 条词库模板`,
    });

    const finalInfo = await pollGenerateProgress(batchId);
    setIsGenerating(false);
    if (finalInfo?.status === "completed") {
      toast.success("任务创建完成，可前往「批量生图」执行生成");
      await loadPrompts();
    } else if (finalInfo?.status === "cancelled") {
      toast.info("任务创建已中断");
    } else {
      toast.error("任务创建失败或超时");
    }
  }, [batchId, expandedType, pollGenerateProgress, promptCount, promptMap, selectedImageId, loadPrompts]);

  const handleCancelGenerate = useCallback(async () => {
    if (!batchId) return;
    const result = await promptApi.cancel(batchId);
    if (result !== undefined) {
      toast.info("已发送中断请求");
    }
  }, [batchId]);

  const handleUpdatePrompt = useCallback(
    (promptId: string, field: "positive_prompt" | "negative_prompt" | "style_name", value: string) => {
      setPromptMap((prev) => {
        const next = { ...prev };
        for (const key of Object.keys(next)) {
          next[key] = next[key].map((p) => (p.id === promptId ? { ...p, [field]: value } : p));
        }
        return next;
      });

      if (!batchId) return;
      if (saveTimerRef.current[promptId]) {
        clearTimeout(saveTimerRef.current[promptId]);
      }
      saveTimerRef.current[promptId] = setTimeout(async () => {
        const data: Record<string, string> = { [field]: value };
        await promptApi.edit(promptId, data);
        delete saveTimerRef.current[promptId];
      }, 700);
    },
    [batchId],
  );

  const handleSave = useCallback(() => {
    for (const [id, timer] of Object.entries(saveTimerRef.current)) {
      clearTimeout(timer);
      delete saveTimerRef.current[id];
    }
    toast.success("提示词已保存");
  }, []);

  const handleDeletePrompt = useCallback(async (typeId: string, promptId: string) => {
    if (batchId) {
      await promptApi.delete(promptId);
    }
    setPromptMap((prev) => ({
      ...prev,
      [typeId]: (prev[typeId] || []).filter((p) => p.id !== promptId),
    }));
    toast.info("已删除提示词");
  }, [batchId]);

  const handleDeleteTypePrompts = useCallback(async () => {
    if (!expandedType) {
      toast.info("请先选择人群类型");
      return;
    }
    const prompts = promptMap[expandedType] || [];
    if (prompts.length === 0) {
      toast.info("当前类型暂无提示词");
      return;
    }
    const ok = window.confirm(`确定清空「${allTypes.find((t) => t.id === expandedType)?.name || expandedType}」全部提示词吗？`);
    if (!ok) return;
    if (batchId) {
      await promptApi.deleteByCrowd(expandedType);
    }
    setPromptMap((prev) => ({ ...prev, [expandedType]: [] }));
    toast.success("已清空当前类型");
  }, [batchId, expandedType, promptMap]);

  const handleAddPrompt = useCallback(async () => {
    if (!expandedType) {
      toast.info("请先选择人群类型");
      return;
    }
    if (!batchId) {
      toast.error("请先上传并选择一个批次");
      return;
    }
    const typeName = allTypes.find((t) => t.id === expandedType)?.name || expandedType;
    const currentCount = (promptMap[expandedType] || []).length;
    const styleName = `${typeName}穿搭${String(currentCount + 1).padStart(2, "0")}`;

    const positive = `人物类型：${typeName}。保持原图背景、光影、景点和机位完全一致，仅替换人物主体；重点描述服装款式、面料层次、发型、配饰、动作pose、景别和站位。`;
    const negative = "禁止更换背景地点、禁止改变光影方向、禁止多人物、禁止遮挡脸部";

    const created = await promptApi.create({
      crowd_type: expandedType,
      style_name: styleName,
      positive_prompt: positive,
      negative_prompt: negative,
      reference_weight: 90,
      preferred_engine: "seedream",
      is_active: true,
    });
    if (created) {
      toast.success("已新增提示词，请继续编辑细节");
      await loadPrompts();
    }
  }, [batchId, expandedType, promptMap, loadPrompts]);

  const handleDownloadTemplate = useCallback(() => {
    const fallbackType = expandedType || "C02";
    const typeName = allTypes.find((t) => t.id === fallbackType)?.name || fallbackType;
    const csvContent = [
      "crowd_type,style_name,positive_prompt,negative_prompt,reference_weight,preferred_engine,is_active",
      `${fallbackType},"${typeName}穿搭01","保持原图背景和光影不变，仅替换${typeName}人物主体；服装：新中式套装，发型：简洁盘发，姿态：自然站立，景别：半身，站位：画面右侧三分之一","背景替换,地标变更,多人物,脸部遮挡",90,seedream,true`,
      `${fallbackType},"${typeName}穿搭02","保持原图背景和光影不变，仅替换${typeName}人物主体；服装：都市轻通勤，发型：低马尾，姿态：扶栏轻倚，景别：全身，站位：前景偏左","背景替换,地标变更,多人物,脸部遮挡",90,seedream,true`,
    ].join("\n");

    const blob = new Blob(["\uFEFF" + csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "prompt-template.csv";
    a.click();
    URL.revokeObjectURL(url);
    toast.success("已下载导入模板");
  }, [expandedType]);

  const handleImportClick = useCallback(() => {
    importInputRef.current?.click();
  }, []);

  const handleImportChange = useCallback(async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    if (!batchId) {
      toast.error("请先上传并选择一个批次");
      event.target.value = "";
      return;
    }

    const targetType = expandedType || undefined;
    const replaceCurrent = !!targetType
      && window.confirm("是否覆盖当前人群已有词条？选择“取消”则为追加导入。");

    setIsImporting(true);
    const result = await promptApi.importTemplates(file, targetType, replaceCurrent);
    setIsImporting(false);
    event.target.value = "";

    if (result) {
      toast.success(`导入完成：新增 ${result.created_count}，更新 ${result.updated_count}`);
      if (result.error_count > 0) {
        toast.warning(`有 ${result.error_count} 行导入失败，请检查模板格式`);
      }
      await loadPrompts();
    }
  }, [batchId, expandedType, loadPrompts]);

  const getTypePromptCount = (typeId: string) => promptMap[typeId]?.length || 0;
  const currentPrompts = expandedType ? (promptMap[expandedType] || []) : [];

  return (
    <MainLayout
      title="提示词词库"
      actions={
        <div className="flex items-center gap-3">
          {isGenerating && genProgress && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>{genProgress.progress}% ({genProgress.completed}/{genProgress.total})</span>
            </div>
          )}
          <Button variant="outline" size="sm" onClick={handleSave}>
            <Save className="w-4 h-4 mr-2" />
            保存配置
          </Button>
          <Button variant="outline" size="sm" onClick={handleDownloadTemplate}>
            <Download className="w-4 h-4 mr-2" />
            下载模板
          </Button>
          <Button variant="outline" size="sm" onClick={handleImportClick} disabled={isImporting}>
            {isImporting ? (
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
            ) : (
              <Upload className="w-4 h-4 mr-2" />
            )}
            导入词库
          </Button>
          {isGenerating && (
            <Button variant="destructive" size="sm" onClick={handleCancelGenerate}>
              <Pause className="w-4 h-4 mr-2" />
              中断
            </Button>
          )}
          <Button size="sm" onClick={handleApplyCurrentType} disabled={isGenerating || !expandedType}>
            {isGenerating ? (
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
            ) : (
              <Wand2 className="w-4 h-4 mr-2" />
            )}
            {isGenerating ? "创建中..." : "按当前词库创建任务"}
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
              disabled={isGenerating}
            />
          </div>
        </div>
      }
    >
      <input
        ref={importInputRef}
        type="file"
        className="hidden"
        accept=".csv,.json"
        onChange={handleImportChange}
      />

      <div className="flex gap-4 h-[calc(100vh-140px)]">
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
                          : "hover:ring-2 hover:ring-muted-foreground/30",
                      )}
                      onClick={() => setSelectedImageId(image.id)}
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
                    <p className="text-xs text-muted-foreground text-center py-4">暂无底图</p>
                  )}
                </div>
              )}
            </ScrollArea>
          </CardContent>
        </Card>

        <Card className="w-[280px] shrink-0 flex flex-col">
          <CardHeader className="py-2 shrink-0">
            <CardTitle className="text-base">人群类型</CardTitle>
          </CardHeader>
          <CardContent className="p-0 flex-1 overflow-hidden">
            <ScrollArea className="h-full">
              <div className="px-4 pb-4">
                <div className="mb-4">
                  <h4 className="text-sm font-medium text-muted-foreground mb-2 px-2">单人类型（7种）</h4>
                  <div className="space-y-1">
                    {crowdTypes.single.map((type) => (
                      <div
                        key={type.id}
                        className={cn(
                          "flex items-center gap-3 px-3 py-2 rounded-lg cursor-pointer transition-colors",
                          expandedType === type.id ? "bg-accent" : "hover:bg-muted",
                        )}
                        onClick={() => setExpandedType(expandedType === type.id ? null : type.id)}
                      >
                        <div className="flex-1">
                          <div className="flex items-center justify-between">
                            <span className="text-sm font-medium">{type.name}</span>
                            <span className="text-xs text-muted-foreground">{getTypePromptCount(type.id)} 个</span>
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

        <Card className="flex-1 flex flex-col">
          <CardHeader className="py-2 shrink-0">
            <div className="flex items-center justify-between">
              <CardTitle className="text-base">
                {expandedType
                  ? `${allTypes.find((t) => t.id === expandedType)?.name} - 词库列表`
                  : "请选择人群类型"}
              </CardTitle>
              {expandedType && (
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={handleAddPrompt}>
                    <Plus className="w-4 h-4 mr-2" />
                    新增词条
                  </Button>
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={handleDeleteTypePrompts}
                    disabled={isGenerating || currentPrompts.length === 0}
                  >
                    <Trash2 className="w-4 h-4 mr-2" />
                    清空当前类型
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
                                onChange={(e) => handleUpdatePrompt(prompt.id, "style_name", e.target.value)}
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

                        <div className="mb-2">
                          <label className="text-xs text-muted-foreground mb-1 block">正向提示词</label>
                          <Textarea
                            value={prompt.positive_prompt}
                            onChange={(e) => handleUpdatePrompt(prompt.id, "positive_prompt", e.target.value)}
                            rows={4}
                            className="resize-none text-sm"
                            placeholder="输入正向提示词..."
                          />
                        </div>

                        <div className="mb-2">
                          <label className="text-xs text-muted-foreground mb-1 block">负向提示词</label>
                          <Textarea
                            value={prompt.negative_prompt || ""}
                            onChange={(e) => handleUpdatePrompt(prompt.id, "negative_prompt", e.target.value)}
                            rows={2}
                            className="resize-none text-sm"
                            placeholder="输入负向提示词..."
                          />
                        </div>

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
                        当前类型暂无词库，请点击「新增词条」或「导入词库」
                      </p>
                      <div className="flex items-center justify-center gap-2">
                        <Button variant="outline" onClick={handleAddPrompt}>
                          <Plus className="w-4 h-4 mr-2" />
                          新增词条
                        </Button>
                        <Button variant="outline" onClick={handleImportClick}>
                          <Upload className="w-4 h-4 mr-2" />
                          导入词库
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              </ScrollArea>
            ) : (
              <div className="flex items-center justify-center h-[calc(100vh-320px)]">
                <p className="text-muted-foreground">请从左侧选择一个人群类型查看词库</p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {isGenerating && genProgress && (
        <div className="fixed bottom-6 right-6 w-80 bg-popover border rounded-lg shadow-lg p-4 z-50">
          <div className="flex items-center gap-2 mb-2">
            <Loader2 className="w-4 h-4 animate-spin" />
            <span className="text-sm font-medium">任务创建中...</span>
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
