import { useState, useRef, useEffect } from "react";
import { MainLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { toast } from "sonner";
import { Play, RefreshCw, Save, Check, ChevronLeft, ChevronRight, Loader2, Pause } from "lucide-react";
import { cn } from "@/lib/utils";
import { widefaceApi, templateApi, toFileUrl, type TemplateItem } from "@/lib/api";
import { useUpload } from "@/contexts/UploadContext";
import { useLocation } from "wouter";

interface WideFaceImage {
  id: string;
  originalUrl: string;
  wideFaceUrl: string;
  crowdType: string;
  selected: boolean;
  wideFaceStatus: string | null;
}

// 宽脸图适用人群类型：在原5类基础上补充幼女(C01)
const SINGLE_CROWD_TYPE_IDS = ["C01", "C02", "C03", "C04", "C06", "C07"] as const;

/** 将 TemplateItem[] 映射为 WideFaceImage[] */
function templatesToImages(items: TemplateItem[]): WideFaceImage[] {
  return items.map((item) => ({
    id: item.id,
    originalUrl: toFileUrl(item.original_path) || templateApi.imageUrl(item.id),
    wideFaceUrl: item.wide_face_path ? toFileUrl(item.wide_face_path) : "",
    crowdType: item.crowd_name,
    selected: true,
    wideFaceStatus: item.wide_face_status,
  }));
}

export default function WideFace() {
  const { batchId } = useUpload();
  const [, setLocation] = useLocation();
  const [loading, setLoading] = useState(false);
  const [images, setImages] = useState<WideFaceImage[]>([]);
  const [isGenerating, setIsGenerating] = useState(false);
  const [progress, setProgress] = useState(0);
  const [isAllSelected, setIsAllSelected] = useState(true);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 派生计数
  const selectedCount = images.filter((img) => img.selected).length;
  const unselectedCount = images.filter((img) => !img.selected).length;
  const totalImages = images.length;
  const completedImages = images.filter((img) => img.wideFaceStatus === "completed").length;
  const progressPercentage = totalImages > 0 ? Math.round((completedImages / totalImages) * 100) : 0;

  // ---- 加载模板数据 ----
  useEffect(() => {
    if (!batchId) {
      setImages([]);
      setLoading(false);
      return;
    }
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const result = await templateApi.list({ final_status: "selected", batch_id: batchId, page_size: 500 });
        if (cancelled) return;
        if (result?.items && result.items.length > 0) {
          const filtered = result.items.filter((t) =>
            SINGLE_CROWD_TYPE_IDS.includes(t.crowd_type as (typeof SINGLE_CROWD_TYPE_IDS)[number]),
          );
          if (filtered.length > 0) {
            const mapped = templatesToImages(filtered);
            setImages(mapped);
            setIsAllSelected(mapped.length > 0 && mapped.every((img) => img.selected));
            setLoading(false);
            return;
          }
        }
      } catch {
        if (cancelled) return;
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [batchId]);

  // ---- 轮询宽脸图状态 ----
  const stopPolling = () => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  };

  /** 从后端刷新模板列表，更新图片状态 */
  const refreshTemplates = async () => {
    if (!batchId) {
      setImages([]);
      return;
    }
    try {
      const result = await templateApi.list({ final_status: "selected", batch_id: batchId, page_size: 500 });
      if (!result?.items) return;
      const filtered = result.items.filter((t) =>
        SINGLE_CROWD_TYPE_IDS.includes(t.crowd_type as (typeof SINGLE_CROWD_TYPE_IDS)[number]),
      );
      setImages((prev) => {
        const selectedMap = new Map(prev.map((img) => [img.id, img.selected] as const));
        const merged = templatesToImages(filtered).map((img) => ({
          ...img,
          selected: selectedMap.get(img.id) ?? true,
        }));
        setIsAllSelected(merged.length > 0 && merged.every((img) => img.selected));
        return merged;
      });
    } catch { /* ignore */ }
  };

  const startPolling = () => {
    if (pollingRef.current) return;
    pollingRef.current = setInterval(async () => {
      try {
        const info = await widefaceApi.progress();
        if (!info) return;

        setProgress(info.progress ?? 0);

        if (info.status === "completed" || info.status === "error" || info.status === "cancelled") {
          stopPolling();
          await refreshTemplates();
          setIsGenerating(false);
          setProgress(info.status === "completed" ? 100 : (info.progress ?? 0));
          if (info.status === "completed") {
            toast.success(`宽脸图生成完成！成功 ${info.completed} 张，失败 ${info.failed} 张`);
          } else if (info.status === "cancelled") {
            toast.info("宽脸图生成已中断，剩余任务保留为待生成");
          } else {
            toast.error("宽脸图生成出错，请检查后端日志");
          }
        }
      } catch (err) {
        console.error("Polling error:", err);
      }
    }, 2000);
  };

  // ---- 刷新恢复：检查是否有正在运行的宽脸任务 ----
  useEffect(() => {
    (async () => {
      try {
        const info = await widefaceApi.progress();
        if (info && (info.status === "running" || info.status === "cancelling")) {
          setIsGenerating(true);
          setProgress(info.progress ?? 0);
          startPolling();
        }
      } catch { /* ignore */ }
    })();
    return () => stopPolling();
  }, []);

  // ---- 图片选择切换 ----
  const handleToggleImage = (id: string) => {
    setImages((prev) => {
      const updated = prev.map((img) => (img.id === id ? { ...img, selected: !img.selected } : img));
      setIsAllSelected(updated.length > 0 && updated.every((i) => i.selected));
      return updated;
    });
  };

  // ---- 开始生成 ----
  const handleStartGeneration = async () => {
    if (images.length === 0) {
      toast.info("选用库中没有符合条件的模板图");
      return;
    }

    const needGen = images.filter((img) => !img.wideFaceUrl && img.wideFaceStatus !== "processing");
    if (needGen.length === 0) {
      toast.info("所有图片都已生成宽脸图");
      return;
    }

    try {
      setIsGenerating(true);
      setProgress(0);
      toast.info(`开始生成 ${needGen.length} 张宽脸图...`);
      await widefaceApi.generate(needGen.map((img) => img.id));
      startPolling();
    } catch {
      toast.error("宽脸图生成请求失败，请重试");
      setIsGenerating(false);
    }
  };

  // ---- 重新生成未选中 ----
  const handleRegenerate = async () => {
    if (unselectedCount === 0) {
      toast.info("所有图片都已选中，无需重新生成");
      return;
    }

    try {
      setIsGenerating(true);
      setProgress(0);
      const unselected = images.filter((img) => !img.selected);
      for (const img of unselected) {
        await widefaceApi.review(img.id, "regenerate");
      }
      await widefaceApi.generate(unselected.map((img) => img.id));
      startPolling();
    } catch {
      toast.error("重新生成失败，请重试");
      setIsGenerating(false);
    }
  };

  const handleCancel = async () => {
    const result = await widefaceApi.cancel();
    if (result !== undefined) {
      toast.info("已发送中断请求，任务将在安全点停止");
      setIsGenerating(true);
      startPolling();
    }
  };

  // ---- 全选 / 全不选 ----
  const handleSelectAll = () => {
    const next = !isAllSelected;
    setImages((prev) => prev.map((img) => ({ ...img, selected: next })));
    setIsAllSelected(next);
    toast.info(next ? "已全选所有图片" : "已取消全选");
  };

  // ---- 保存到选用库 ----
  const handleSaveToLibrary = async () => {
    const selected = images.filter((img) => img.selected);
    if (selected.length === 0) {
      toast.error("请先选择要保存的图片");
      return;
    }

    try {
      for (const img of selected) {
        await widefaceApi.review(img.id, "pass");
      }
      const remaining = images.filter((img) => !img.selected);
      setImages(remaining);
      toast.success(`已保存 ${selected.length} 张宽脸图到选用库`);
      if (remaining.length === 0) {
        setProgress(0);
        setIsGenerating(false);
      }
    } catch {
      toast.error("保存失败，请重试");
    }
  };

  const handleSkipWideFace = () => {
    setLocation("/process");
  };

  // ---- 水平滚动 ----
  const scroll = (direction: "left" | "right") => {
    scrollContainerRef.current?.scrollBy({
      left: direction === "left" ? -400 : 400,
      behavior: "smooth",
    });
  };

  return (
    <MainLayout title="宽脸图">
      <div className="flex flex-col h-[calc(100vh-140px)]">
        {/* 顶部：生成按钮和进度条 */}
        <div className="mb-6 p-4 bg-white rounded-lg border">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <Button
                onClick={handleStartGeneration}
                disabled={isGenerating || loading}
                className="bg-primary hover:bg-primary/90"
              >
                {loading ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Play className="w-4 h-4 mr-2" />}
                开始生成宽脸图
              </Button>
              {isGenerating && (
                <Button variant="destructive" onClick={handleCancel}>
                  <Pause className="w-4 h-4 mr-2" />
                  中断生成
                </Button>
              )}
              <Button variant="outline" onClick={handleSkipWideFace} disabled={isGenerating}>
                跳过宽脸，直接画面处理
              </Button>
            </div>
            <span className="text-sm text-muted-foreground">
              选用库 - {SINGLE_CROWD_TYPE_IDS.length}类单人照，共{totalImages}张图片 | 进度: {progressPercentage}% ({completedImages}/{totalImages})
            </span>
          </div>

          {isGenerating && (
            <div className="w-full">
              <Progress value={progressPercentage} className="h-2" />
              <p className="text-xs text-muted-foreground mt-2">正在生成中，请稍候...</p>
            </div>
          )}

          {!isGenerating && progress > 0 && progress < 100 && (
            <div className="text-xs text-muted-foreground">生成已暂停</div>
          )}

          {progressPercentage === 100 && !isGenerating && images.length > 0 && (
            <p className="text-xs text-green-600">✓ 生成完成！</p>
          )}
        </div>

        {/* 主要区域：一行水平滚动图片 */}
        {images.length === 0 ? (
          <div className="flex-1 flex items-center justify-center mb-6">
            <div className="text-center">
              <p className="text-muted-foreground text-lg">
                {loading ? "正在加载..." : "点击\"开始生成宽脸图\"按钮生成图片"}
              </p>
              {!loading && <p className="text-sm text-muted-foreground mt-2">选用库中暂无可处理模板图</p>}
            </div>
          </div>
        ) : (
          <div className="flex-1 mb-6 relative">
            <button
              onClick={() => scroll("left")}
              className="absolute left-0 top-1/2 -translate-y-1/2 z-10 bg-white/80 hover:bg-white rounded-full p-2 shadow-md transition-all"
            >
              <ChevronLeft className="w-5 h-5" />
            </button>

            <div
              ref={scrollContainerRef}
              className="w-full h-full overflow-x-auto overflow-y-hidden scroll-smooth"
              style={{ scrollBehavior: "smooth" }}
            >
              <div className="flex gap-4 px-16 py-4 h-full">
                {images.map((image) => {
                  const imageUrl = image.wideFaceUrl || image.originalUrl;
                  return (
                    <div
                      key={image.id}
                      onClick={() => handleToggleImage(image.id)}
                      className={cn(
                        "relative flex-shrink-0 aspect-[9/16] rounded-lg overflow-hidden cursor-pointer transition-all",
                        "w-[calc((100vh-300px)*9/16)]",
                        image.selected
                          ? "ring-4 ring-primary shadow-lg"
                          : "ring-2 ring-gray-200 opacity-60 hover:opacity-100",
                      )}
                    >
                      <img src={imageUrl} alt={`${image.crowdType} - ${image.id}`} className="w-full h-full object-cover" />
                      {image.selected && (
                        <div className="absolute top-3 right-3 w-7 h-7 rounded-full bg-primary flex items-center justify-center shadow-lg">
                          <Check className="w-5 h-5 text-white" />
                        </div>
                      )}
                      {image.wideFaceStatus === "processing" && (
                        <div className="absolute inset-0 bg-black/50 flex items-center justify-center">
                          <Loader2 className="w-6 h-6 text-white animate-spin" />
                        </div>
                      )}
                      {image.wideFaceStatus === "failed" && (
                        <div className="absolute inset-0 bg-red-500/30 flex items-center justify-center">
                          <span className="text-white text-sm font-medium">生成失败</span>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            <button
              onClick={() => scroll("right")}
              className="absolute right-0 top-1/2 -translate-y-1/2 z-10 bg-white/80 hover:bg-white rounded-full p-2 shadow-md transition-all"
            >
              <ChevronRight className="w-5 h-5" />
            </button>
          </div>
        )}

        {/* 底部：操作按钮 */}
        {images.length > 0 && (
          <div className="border-t pt-4">
            <div className="flex items-center justify-between">
              <div className="flex gap-3">
                <Button onClick={handleSaveToLibrary} className="bg-primary hover:bg-primary/90" disabled={selectedCount === 0}>
                  <Save className="w-4 h-4 mr-2" />
                  保存到选用库
                </Button>
                <Button onClick={handleRegenerate} variant="outline" disabled={isGenerating || unselectedCount === 0}>
                  <RefreshCw className="w-4 h-4 mr-2" />
                  重新生成
                </Button>
                <Button onClick={handleSelectAll} variant="outline">
                  <Check className="w-4 h-4 mr-2" />
                  {isAllSelected ? "全不选" : "全选"}
                </Button>
              </div>
              <div className="text-xs text-muted-foreground">
                <p>快捷键: Enter=开始生成 | R=重新生成 | Space=全选 | 点击图片=切换选中</p>
              </div>
            </div>
          </div>
        )}
      </div>
    </MainLayout>
  );
}
