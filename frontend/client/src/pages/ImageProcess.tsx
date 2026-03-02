import { useState, useEffect, useCallback } from "react";
import { MainLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import { Download, MoreHorizontal, Play, FileArchive, RefreshCw, AlertCircle, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import JSZip from "jszip";
import { compressApi, templateApi, toFileUrl, type TemplateItem } from "@/lib/api";
import { useUpload } from "@/contexts/UploadContext";

// 处理状态
type ProcessStatus = "idle" | "processing" | "completed";
type ImageStatus = "pending" | "enhancing" | "compressing" | "completed" | "failed_enhance" | "failed_compress";

interface ProcessImage {
  id: string;
  url: string;
  processedUrl?: string;
  crowdType: string;
  status: ImageStatus;
  isWideFace?: boolean;
  pairId?: string;
}

// 19种人群类型
const crowdTypes = [
  "幼女", "少女", "熟女", "奶奶",
  "幼男", "少男", "大叔",
  "情侣", "闺蜜", "兄弟", "异性伙伴",
  "母子(少年)", "母子(幼年)", "母女(少年)", "母女(幼年)",
  "父子(少年)", "父子(幼年)", "父女(少年)", "父女(幼年)",
];


/** Map TemplateItem.compress_status to local ImageStatus */
function mapCompressStatus(status: string | null): ImageStatus {
  if (!status || status === "pending") return "pending";
  if (status === "processing") return "compressing";
  if (status === "completed") return "completed";
  if (status === "failed") return "failed_compress";
  return "pending";
}

/** Convert API TemplateItem[] to ProcessImage[] */
function mapTemplatesToImages(items: TemplateItem[]): ProcessImage[] {
  const images: ProcessImage[] = [];
  for (const item of items) {
    const hasWideFace = !!item.wide_face_path;
    images.push({
      id: item.id,
      url: toFileUrl(item.original_path) || templateApi.imageUrl(item.id),
      processedUrl: item.compressed_path ? toFileUrl(item.compressed_path) : undefined,
      crowdType: item.crowd_name,
      status: mapCompressStatus(item.compress_status),
      isWideFace: false,
      pairId: hasWideFace ? `${item.id}_wide` : undefined,
    });
    if (hasWideFace) {
      images.push({
        id: `${item.id}_wide`,
        url: toFileUrl(item.wide_face_path),
        crowdType: item.crowd_name,
        status: mapCompressStatus(item.compress_status),
        isWideFace: true,
        pairId: item.id,
      });
    }
  }
  return images;
}

export default function ImageProcess() {
  const { batchId } = useUpload();
  const [images, setImages] = useState<ProcessImage[]>([]);
  const [processStatus, setProcessStatus] = useState<ProcessStatus>("idle");
  const [progress, setProgress] = useState(0);
  const [selectedCrowd, setSelectedCrowd] = useState<string>("全部");
  const [previewImage, setPreviewImage] = useState<ProcessImage | null>(null);
  const [loading, setLoading] = useState(false);
  const [apiAvailable, setApiAvailable] = useState(false);

  // ---------- Load templates ----------
  const loadTemplates = useCallback(async () => {
    if (!batchId) {
      setApiAvailable(false);
      setImages([]);
      return;
    }
    setLoading(true);
    try {
      const result = await templateApi.list({ final_status: "selected", batch_id: batchId, page_size: 500 });
      if (result?.items?.length) {
        setApiAvailable(true);
        setImages(mapTemplatesToImages(result.items));
      } else {
        setApiAvailable(false);
        setImages([]);
      }
    } catch {
      setApiAvailable(false);
      setImages([]);
    } finally {
      setLoading(false);
    }
  }, [batchId]);

  useEffect(() => {
    loadTemplates();
  }, [loadTemplates]);

  // ---------- Poll compress progress ----------
  const pollProgress = useCallback(async () => {
    const INTERVAL = 2000;
    const MAX_POLLS = 300;
    let polls = 0;
    while (polls < MAX_POLLS) {
      await new Promise((r) => setTimeout(r, INTERVAL));
      try {
        const info = await compressApi.progress();
        if (!info) break;
        if (info.total > 0) {
          setProgress(Math.round((info.completed / info.total) * 100));
        }
        if (info.status === "completed" || info.status === "error") return info;
      } catch {
        break;
      }
      polls++;
    }
    return null;
  }, []);

  // ---------- Start processing ----------
  const startProcessing = async () => {
    if (processStatus === "processing") return;
    setProcessStatus("processing");
    setProgress(0);

    setImages((prev) =>
      prev.map((img) => (img.status === "pending" ? { ...img, status: "compressing" as ImageStatus } : img)),
    );

    if (apiAvailable) {
      try {
        await compressApi.start();
        toast.success("压缩任务已启动");
        const result = await pollProgress();
        if (result?.status === "completed") {
          toast.success(`压缩完成: ${result.completed} 张成功, ${result.failed} 张失败`);
        } else if (result?.status === "error") {
          toast.error("压缩过程中出现错误");
        } else {
          toast.warning("压缩轮询超时，请检查后端状态");
        }
        await loadTemplates();
        setProcessStatus("completed");
        setProgress(100);
      } catch {
        toast.error("启动压缩失败");
        setProcessStatus("idle");
        setImages((prev) =>
          prev.map((img) => (img.status === "compressing" ? { ...img, status: "pending" as ImageStatus } : img)),
        );
      }
    } else {
      // Mock simulation
      const pendingImages = images.filter((img) => img.status === "pending");
      const total = pendingImages.length;
      for (let i = 0; i < total; i++) {
        const img = pendingImages[i];
        await new Promise((r) => setTimeout(r, 300 + Math.random() * 500));
        const success = Math.random() > 0.1;
        setImages((prev) =>
          prev.map((item) =>
            item.id === img.id
              ? { ...item, status: success ? ("completed" as ImageStatus) : ("failed_compress" as ImageStatus), processedUrl: success ? item.url : undefined }
              : item,
          ),
        );
        setProgress(Math.round(((i + 1) / total) * 100));
      }
      setProcessStatus("completed");
      toast.success("处理完成");
    }
  };

  // ---------- Retry single image ----------
  const retryImage = async (imageId: string) => {
    setImages((prev) =>
      prev.map((img) => (img.id === imageId ? { ...img, status: "compressing" as ImageStatus } : img)),
    );
    if (apiAvailable) {
      try {
        await compressApi.retry(imageId);
        toast.success("重试已提交");
        await loadTemplates();
      } catch {
        toast.error("重试失败");
        setImages((prev) =>
          prev.map((img) => (img.id === imageId ? { ...img, status: "failed_compress" as ImageStatus } : img)),
        );
      }
    } else {
      await new Promise((r) => setTimeout(r, 1000));
      const success = Math.random() > 0.3;
      setImages((prev) =>
        prev.map((img) =>
          img.id === imageId
            ? { ...img, status: success ? ("completed" as ImageStatus) : ("failed_compress" as ImageStatus), processedUrl: success ? img.url : undefined }
            : img,
        ),
      );
      if (success) toast.success("重试成功");
      else toast.error("重试失败");
    }
  };

  // ---------- Export ZIP ----------
  const exportAsZip = async () => {
    const completedImages = images.filter((img) => img.status === "completed" && img.processedUrl);
    if (completedImages.length === 0) {
      toast.error("没有已完成的图片可导出");
      return;
    }
    toast.info("正在打包导出...");
    const zip = new JSZip();
    for (const img of completedImages) {
      try {
        const response = await fetch(img.processedUrl!);
        const blob = await response.blob();
        const ext = blob.type.includes("png") ? "png" : "jpg";
        const folder = img.isWideFace ? "宽脸图" : "原图";
        zip.file(`${img.crowdType}/${folder}/${img.id}.${ext}`, blob);
      } catch { /* skip */ }
    }
    const content = await zip.generateAsync({ type: "blob" });
    const url = URL.createObjectURL(content);
    const a = document.createElement("a");
    a.href = url;
    a.download = `processed_images_${new Date().toISOString().slice(0, 10)}.zip`;
    a.click();
    URL.revokeObjectURL(url);
    toast.success("导出完成");
  };

  // ---------- Derived data ----------
  const availableCrowds = ["全部", ...Array.from(new Set(images.map((img) => img.crowdType)))];
  const filteredImages = selectedCrowd === "全部" ? images : images.filter((img) => img.crowdType === selectedCrowd);

  // Group images into pairs
  const imagePairs: { original: ProcessImage; wideFace?: ProcessImage }[] = [];
  const processed = new Set<string>();
  for (const img of filteredImages) {
    if (processed.has(img.id)) continue;
    if (!img.isWideFace) {
      const pair = img.pairId ? filteredImages.find((i) => i.id === img.pairId) : undefined;
      imagePairs.push({ original: img, wideFace: pair });
      processed.add(img.id);
      if (pair) processed.add(pair.id);
    }
  }
  for (const img of filteredImages) {
    if (!processed.has(img.id)) {
      imagePairs.push({ original: img });
      processed.add(img.id);
    }
  }

  const completedCount = images.filter((img) => img.status === "completed").length;
  const failedCount = images.filter((img) => img.status === "failed_enhance" || img.status === "failed_compress").length;
  const totalCount = images.length;

  return (
    <MainLayout title="画质处理">
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <p className="text-muted-foreground mt-1">
              对选用库图片进行画质增强和压缩处理
              {apiAvailable && <span className="ml-2 text-xs text-green-600">[API 已连接]</span>}
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={exportAsZip} disabled={completedCount === 0}>
              <FileArchive className="w-4 h-4 mr-2" />
              导出ZIP
            </Button>
            <Button onClick={startProcessing} disabled={processStatus === "processing" || loading}>
              {processStatus === "processing" ? (
                <><Loader2 className="w-4 h-4 mr-2 animate-spin" />处理中...</>
              ) : (
                <><Play className="w-4 h-4 mr-2" />开始处理</>
              )}
            </Button>
          </div>
        </div>

        {/* Progress bar */}
        {processStatus === "processing" && (
          <div className="space-y-2">
            <div className="flex justify-between text-sm">
              <span>处理进度</span>
              <span>{progress}%</span>
            </div>
            <div className="w-full bg-secondary rounded-full h-2">
              <div className="bg-primary h-2 rounded-full transition-all duration-300" style={{ width: `${progress}%` }} />
            </div>
          </div>
        )}

        {/* Stats bar */}
        {processStatus !== "idle" && (
          <div className="flex gap-4 text-sm">
            <span>总计: {totalCount}</span>
            <span className="text-green-600">完成: {completedCount}</span>
            {failedCount > 0 && <span className="text-red-600">失败: {failedCount}</span>}
          </div>
        )}

        {/* Crowd type tabs */}
        <ScrollArea className="w-full whitespace-nowrap">
          <div className="flex gap-2 pb-2">
            {availableCrowds.map((crowd) => (
              <Button
                key={crowd}
                variant={selectedCrowd === crowd ? "default" : "outline"}
                size="sm"
                onClick={() => setSelectedCrowd(crowd)}
                className="shrink-0"
              >
                {crowd}
                {crowd !== "全部" && (
                  <span className="ml-1 text-xs opacity-70">
                    ({images.filter((img) => img.crowdType === crowd && !img.isWideFace).length})
                  </span>
                )}
              </Button>
            ))}
          </div>
          <ScrollBar orientation="horizontal" />
        </ScrollArea>

        {/* Loading state */}
        {loading && (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
            <span className="ml-3 text-muted-foreground">加载模板数据...</span>
          </div>
        )}

        {/* Empty state */}
        {!loading && images.length === 0 && (
          <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
            <AlertCircle className="w-12 h-12 mb-4" />
            <p>选用库中暂无图片</p>
            <p className="text-sm mt-1">请先在审核分类页面选用图片</p>
          </div>
        )}

        {/* Image grid */}
        {!loading && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {imagePairs.map(({ original, wideFace }) => (
              <div key={original.id} className="border rounded-lg overflow-hidden bg-card">
                <div className="grid grid-cols-2 gap-0.5 bg-muted">
                  {/* Original */}
                  <div className="relative aspect-[3/4] cursor-pointer group" onClick={() => setPreviewImage(original)}>
                    <img src={original.processedUrl || original.url} alt="原图" className="w-full h-full object-cover" loading="lazy" />
                    <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-colors" />
                    <span className="absolute top-1 left-1 text-[10px] bg-black/60 text-white px-1 rounded">原图</span>
                    <StatusBadge status={original.status} />
                  </div>
                  {/* Wide face */}
                  <div
                    className={cn("relative aspect-[3/4] cursor-pointer group", !wideFace && "flex items-center justify-center bg-muted")}
                    onClick={() => wideFace && setPreviewImage(wideFace)}
                  >
                    {wideFace ? (
                      <>
                        <img src={wideFace.processedUrl || wideFace.url} alt="宽脸图" className="w-full h-full object-cover" loading="lazy" />
                        <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-colors" />
                        <span className="absolute top-1 left-1 text-[10px] bg-blue-600/80 text-white px-1 rounded">宽脸</span>
                        <StatusBadge status={wideFace.status} />
                      </>
                    ) : (
                      <span className="text-xs text-muted-foreground">无宽脸图</span>
                    )}
                  </div>
                </div>
                {/* Card footer */}
                <div className="p-2 flex items-center justify-between">
                  <span className="text-xs text-muted-foreground truncate">{original.crowdType}</span>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon" className="h-6 w-6"><MoreHorizontal className="w-3.5 h-3.5" /></Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      {(original.status === "failed_enhance" || original.status === "failed_compress") && (
                        <DropdownMenuItem onClick={() => retryImage(original.id)}>
                          <RefreshCw className="w-3.5 h-3.5 mr-2" />重试
                        </DropdownMenuItem>
                      )}
                      {original.status === "completed" && original.processedUrl && (
                        <DropdownMenuItem onClick={() => {
                          const a = document.createElement("a");
                          a.href = original.processedUrl!;
                          a.download = `${original.id}.jpg`;
                          a.click();
                        }}>
                          <Download className="w-3.5 h-3.5 mr-2" />下载
                        </DropdownMenuItem>
                      )}
                      {wideFace && (wideFace.status === "failed_enhance" || wideFace.status === "failed_compress") && (
                        <DropdownMenuItem onClick={() => retryImage(wideFace.id)}>
                          <RefreshCw className="w-3.5 h-3.5 mr-2" />重试宽脸图
                        </DropdownMenuItem>
                      )}
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Preview dialog */}
        <Dialog open={!!previewImage} onOpenChange={() => setPreviewImage(null)}>
          <DialogContent className="max-w-4xl p-0 overflow-hidden">
            {previewImage && (
              <div className="relative">
                <img src={previewImage.processedUrl || previewImage.url} alt="预览" className="w-full h-auto max-h-[85vh] object-contain" />
                <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/60 to-transparent p-4">
                  <div className="flex items-center justify-between text-white">
                    <div>
                      <span className="text-sm">{previewImage.crowdType}</span>
                      <span className="ml-2 text-xs opacity-70">{previewImage.isWideFace ? "宽脸图" : "原图"}</span>
                    </div>
                    <StatusBadge status={previewImage.status} />
                  </div>
                </div>
              </div>
            )}
          </DialogContent>
        </Dialog>
      </div>
    </MainLayout>
  );
}

function StatusBadge({ status }: { status: ImageStatus }) {
  const config: Record<ImageStatus, { label: string; className: string } | null> = {
    pending: null,
    enhancing: { label: "增强中", className: "bg-blue-500" },
    compressing: { label: "压缩中", className: "bg-blue-500" },
    completed: { label: "完成", className: "bg-green-600" },
    failed_enhance: { label: "增强失败", className: "bg-red-600" },
    failed_compress: { label: "压缩失败", className: "bg-red-600" },
  };
  const c = config[status];
  if (!c) return null;
  return (
    <span className={cn("absolute bottom-1 right-1 text-[10px] text-white px-1 rounded", c.className)}>
      {c.label}
    </span>
  );
}
