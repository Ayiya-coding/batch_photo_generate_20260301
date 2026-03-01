import { useState, useEffect, useCallback, useRef } from "react";
import { MainLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";
import {
  Wand2, Save, ChevronRight, ChevronDown,
  Trash2, Edit3, Sparkles, RefreshCw, ImageIcon, Loader2, Pause,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useUpload } from "@/contexts/UploadContext";
import {
  uploadApi, promptApi, toFileUrl,
  type PromptItem, type ProgressInfo,
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

  // --- 生成进度 ---
  const [isGenerating, setIsGenerating] = useState(false);
  const [genProgress, setGenProgress] = useState<ProgressInfo | null>(null);

  // --- 加载状态 ---
  const [loadingImages, setLoadingImages] = useState(false);
  const [loadingPrompts, setLoadingPrompts] = useState(false);

  // 防抖保存 timer
  const saveTimerRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

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
    if (!batchId) {
      setPromptMap({});
      return;
    }
    setLoadingPrompts(true);
    const result = await promptApi.list({ batch_id: batchId! });
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

    setIsGenerating(true);
    setGenProgress(null);

    const result = await promptApi.generate(batchId!, [expandedType], selectedImageId || undefined);
    if (!result) {
      setIsGenerating(false);
      return;
    }

    const typeName = allTypes.find((t) => t.id === expandedType)?.name || expandedType;
    const selectedImage = images.find((img) => img.id === selectedImageId);
    toast.info("提示词生成已启动", {
      description: `正在为「${typeName}」生成提示词（参考底图：${selectedImage?.filename || "默认首图"}）...`,
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
  }, [batchId, expandedType, selectedImageId, pollGenerateProgress, loadPrompts]);

  // ========== 为单个人群类型重新生成 ==========

  const handleRegenerate = useCallback(async (typeId: string) => {
    if (!batchId) {
      toast.info("演示模式：无法调用后端生成");
      return;
    }

    setIsGenerating(true);
    setGenProgress(null);

    const result = await promptApi.generate(batchId!, [typeId], selectedImageId || undefined);
    if (!result) {
      setIsGenerating(false);
      return;
    }

    const typeName = allTypes.find((t) => t.id === typeId)?.name || typeId;
    toast.info(`正在为「${typeName}」重新生成提示词...`);

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
  }, [batchId, selectedImageId, pollGenerateProgress, loadPrompts]);

  const handleCancelGenerate = useCallback(async () => {
    if (!batchId) return;
    const result = await promptApi.cancel(batchId);
    if (result !== undefined) {
      toast.info("已发送中断请求，任务将在安全点停止");
    }
  }, [batchId]);

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

      // 防抖调用后端保存
      if (batchId) {
        if (saveTimerRef.current[promptId]) {
          clearTimeout(saveTimerRef.current[promptId]);
        }
        saveTimerRef.current[promptId] = setTimeout(async () => {
          const data: Record<string, string> = {};
          if (field === "positive_prompt" || field === "negative_prompt" || field === "style_name") {
            data[field] = value;
          }
          if (Object.keys(data).length > 0) {
            await promptApi.edit(promptId, data);
          }
          delete saveTimerRef.current[promptId];
        }, 800);
      }
    },
    [batchId],
  );

  // ========== 手动保存（清空防抖队列） ==========

  const handleSave = useCallback(async () => {
    // 清空所有待保存的防抖 timer
    for (const [id, timer] of Object.entries(saveTimerRef.current)) {
      clearTimeout(timer);
      delete saveTimerRef.current[id];
    }
    toast.success("保存成功", { description: "提示词配置已保存" });
  }, []);

  // ========== 删除提示词 ==========

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
      toast.info("当前类型暂无可删除的提示词");
      return;
    }

    if (!window.confirm(`确定删除当前类型的 ${prompts.length} 条提示词吗？此操作不可撤销。`)) {
      return;
    }

    if (batchId) {
      await promptApi.deleteByCrowd(expandedType);
    }
    setPromptMap((prev) => ({ ...prev, [expandedType]: [] }));
    const typeName = allTypes.find((t) => t.id === expandedType)?.name || expandedType;
    toast.success(`已清空「${typeName}」提示词`);
  }, [batchId, expandedType, promptMap]);

  // ========== 图片选择 ==========

  const handleImageSelect = useCallback((imageId: string) => {
    setSelectedImageId(imageId);
    const img = images.find((i) => i.id === imageId);
    if (img) {
      toast.info("已切换底图", { description: `当前选中: ${img.filename}` });
    }
  }, [images]);

  // ========== 辅助 ==========

  const getTypePromptCount = (typeId: string) => promptMap[typeId]?.length || 0;
  const currentPrompts = expandedType ? (promptMap[expandedType] || []) : [];

  // ========== 渲染 ==========

  return (
    <MainLayout
      title="提示词"
      actions={
        <div className="flex items-center gap-3">
          {isGenerating && genProgress && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>
                {genProgress.progress}% ({genProgress.completed}/{genProgress.total})
              </span>
            </div>
          )}
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
          <Button size="sm" onClick={handleGenerateSelected} disabled={isGenerating || !expandedType}>
            {isGenerating ? (
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
            ) : (
              <Wand2 className="w-4 h-4 mr-2" />
            )}
            {isGenerating ? "生成中..." : "生成当前类型"}
          </Button>
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

                <div>
                  <h4 className="text-sm font-medium text-muted-foreground mb-2 px-2">
                    组合类型（12种）
                  </h4>
                  <div className="space-y-1">
                    {crowdTypes.combo.map((type) => (
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
                          ? "暂无提示词，请点击「生成当前类型」或「AI重新生成」"
                          : "暂无提示词（演示模式）"}
                      </p>
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
