import { useState, useRef, useEffect, useCallback } from "react";
import { MainLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
import { Download, MoreHorizontal, Eye, Edit, Trash2, Upload, RefreshCw, FileArchive, Eraser, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import JSZip from "jszip";
import { templateApi, toFileUrl, type TemplateItem } from "@/lib/api";
import { useUpload } from "@/contexts/UploadContext";

type ViewType = "approved" | "needEdit" | "recycled";

interface TemplateImage {
  id: string;
  url: string;
  crowdType: string;
  isWideFace?: boolean;
  pairId?: string;
}

// ViewType → API final_status mapping
const STATUS_MAP: Record<ViewType, "selected" | "pending_modification" | "trash"> = {
  approved: "selected",
  needEdit: "pending_modification",
  recycled: "trash",
};

// 19种人群类型
const crowdTypes = [
  "幼女", "少女", "熟女", "奶奶",
  "幼男", "少男", "大叔",
  "情侣", "闺蜜", "兄弟", "异性伙伴",
  "母子(少年)", "母子(幼年)", "母女(少年)", "母女(幼年)",
  "父子(少年)", "父子(幼年)", "父女(少年)", "父女(幼年)",
];

const CROWD_TYPE_ID_MAP: Record<string, string> = {
  幼女: "C01",
  少女: "C02",
  熟女: "C03",
  奶奶: "C04",
  幼男: "C05",
  少男: "C06",
  大叔: "C07",
  情侣: "C08",
  闺蜜: "C09",
  兄弟: "C10",
  异性伙伴: "C11",
  "母子(少年)": "C12",
  "母子(青年)": "C13",
  "母子(幼年)": "C13",
  母子少年: "C12",
  母子青年: "C13",
  "母女(少年)": "C14",
  "母女(青年)": "C15",
  "母女(幼年)": "C15",
  母女少年: "C14",
  母女青年: "C15",
  "父子(少年)": "C16",
  "父子(青年)": "C17",
  "父子(幼年)": "C17",
  父子少年: "C16",
  父子青年: "C17",
  "父女(少年)": "C18",
  "父女(青年)": "C19",
  "父女(幼年)": "C19",
  父女少年: "C18",
  父女青年: "C19",
};

function normalizeCrowdName(name: string): string {
  return name.trim().replace(/（/g, "(").replace(/）/g, ")").replace(/\s+/g, "");
}

function resolveCrowdTypeId(name: string): string {
  const normalized = normalizeCrowdName(name);
  return CROWD_TYPE_ID_MAP[normalized] || "";
}

function isSameCrowdType(left: string, right: string): boolean {
  const leftId = resolveCrowdTypeId(left);
  const rightId = resolveCrowdTypeId(right);
  if (leftId && rightId) return leftId === rightId;
  return normalizeCrowdName(left) === normalizeCrowdName(right);
}


/** Map TemplateItem[] from API to TemplateImage[] */
function mapApiToTemplateImages(items: TemplateItem[]): TemplateImage[] {
  const images: TemplateImage[] = [];
  for (const item of items) {
    const hasWideFace = !!item.wide_face_path;
    images.push({
      id: item.id,
      url: toFileUrl(item.original_path) || templateApi.imageUrl(item.id),
      crowdType: item.crowd_name,
      isWideFace: false,
      pairId: hasWideFace ? `${item.id}_wide` : undefined,
    });
    if (hasWideFace) {
      images.push({
        id: `${item.id}_wide`,
        url: toFileUrl(item.wide_face_path),
        crowdType: item.crowd_name,
        isWideFace: true,
        pairId: item.id,
      });
    }
  }
  return images;
}

/** Strip _wide suffix to get real template ID for API calls */
function realId(id: string): string {
  return id.endsWith("_wide") ? id.slice(0, -5) : id;
}

export default function TemplateManage() {
  const { batchId } = useUpload();
  const [templates, setTemplates] = useState<TemplateImage[]>([]);
  const [loading, setLoading] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isReplacing, setIsReplacing] = useState(false);
  const [currentView, setCurrentView] = useState<ViewType>("approved");
  const [selectedCrowdType, setSelectedCrowdType] = useState<string>("少女");
  const [previewImage, setPreviewImage] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [isExporting, setIsExporting] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const replaceInputRef = useRef<HTMLInputElement>(null);
  const [replaceTargetId, setReplaceTargetId] = useState<string | null>(null);

  // ---- data loading ----
  const loadTemplates = useCallback(async () => {
    setLoading(true);
    try {
      const result = await templateApi.list({
        final_status: STATUS_MAP[currentView],
        batch_id: batchId || undefined,
        page_size: 200,
      });
      if (result?.items && result.items.length > 0) {
        setTemplates(mapApiToTemplateImages(result.items));
      } else {
        setTemplates([]);
      }
    } catch {
      setTemplates([]);
    } finally {
      setLoading(false);
    }
  }, [currentView, batchId]);

  useEffect(() => {
    loadTemplates();
  }, [loadTemplates]);

  // ---- derived state ----
  const filteredTemplates = templates.filter((t) => isSameCrowdType(t.crowdType, selectedCrowdType));

  // ---- actions ----
  const handleViewLarge = (url: string) => setPreviewImage(url);

  const handleDownload = (url: string) => {
    const link = document.createElement("a");
    link.href = url;
    link.download = `image_${Date.now()}.jpg`;
    link.click();
    toast.success("开始下载");
  };

  const handleExportZip = async () => {
    if (templates.length === 0) {
      toast.error("待修改库中没有图片可供导出");
      return;
    }
    setIsExporting(true);
    const toastId = toast.loading("正在准备导出文件...");
    try {
      const zip = new JSZip();
      await Promise.all(
        templates.map(async (img, index) => {
          try {
            const response = await fetch(img.url);
            const blob = await response.blob();
            const extension = blob.type.split("/")[1] || "jpg";
            zip.file(`${img.crowdType}/${img.id}_${index}.${extension}`, blob);
          } catch { /* skip */ }
        }),
      );
      const content = await zip.generateAsync({ type: "blob" });
      const now = new Date();
      const ts = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}${String(now.getDate()).padStart(2, "0")}_${String(now.getHours()).padStart(2, "0")}${String(now.getMinutes()).padStart(2, "0")}${String(now.getSeconds()).padStart(2, "0")}`;
      const link = document.createElement("a");
      link.href = URL.createObjectURL(content);
      link.download = `待修改库_${ts}.zip`;
      link.click();
      toast.dismiss(toastId);
      toast.success("导出成功！");
    } catch {
      toast.dismiss(toastId);
      toast.error("导出失败，请重试");
    } finally {
      setIsExporting(false);
    }
  };

  const handleClearRecycle = async () => {
    if (templates.length === 0) {
      toast.error("回收库已经是空的了");
      return;
    }
    if (!confirm("确定要清空回收库中的所有图片吗？此操作不可撤销。")) return;
    try {
      const originals = templates.filter(t => !t.isWideFace);
      await Promise.all(originals.map(t => templateApi.delete(realId(t.id))));
      toast.success("回收库已清空");
      await loadTemplates();
    } catch {
      toast.error("清空失败，请重试");
    }
  };

  const handleMoveToNeedEdit = async (id: string) => {
    setTemplates(prev => prev.filter(t => t.id !== id && t.pairId !== id));
    try {
      await templateApi.move(realId(id), "pending_modification");
      toast.success("已移至待修改库");
    } catch {
      toast.error("移动失败，请重试");
      await loadTemplates();
    }
  };

  const handleMoveToRecycle = async (id: string) => {
    setTemplates(prev => prev.filter(t => t.id !== id && t.pairId !== id));
    try {
      await templateApi.move(realId(id), "trash");
      toast.success("已移至回收库");
    } catch {
      toast.error("移动失败，请重试");
      await loadTemplates();
    }
  };

  const handleRestore = async (id: string) => {
    setTemplates(prev => prev.filter(t => t.id !== id && t.pairId !== id));
    try {
      await templateApi.move(realId(id), "selected");
      toast.success("已恢复到选用库");
    } catch {
      toast.error("恢复失败，请重试");
      await loadTemplates();
    }
  };

  const handleUploadClick = () => fileInputRef.current?.click();

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    const crowdTypeId = resolveCrowdTypeId(selectedCrowdType);
    if (!crowdTypeId) {
      toast.error(`无法识别人群类型: ${selectedCrowdType}`);
      e.target.value = "";
      return;
    }

    setIsUploading(true);
    const toastId = toast.loading(`正在上传 ${files.length} 张图片...`);
    try {
      const result = await templateApi.upload(Array.from(files), crowdTypeId);
      toast.dismiss(toastId);
      if (result) {
        if (result.failed_count > 0) {
          toast.warning(`上传完成：成功 ${result.uploaded_count} 张，失败 ${result.failed_count} 张`);
        } else {
          toast.success(`已上传 ${result.uploaded_count} 张图片到 ${selectedCrowdType} 分类`);
        }
      }
      await loadTemplates();
    } catch {
      toast.dismiss(toastId);
      toast.error("上传失败，请重试");
    } finally {
      setIsUploading(false);
      e.target.value = "";
    }
  };

  const handleReplaceClick = (id: string) => {
    setReplaceTargetId(id);
    replaceInputRef.current?.click();
  };

  const handleReplaceUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !replaceTargetId) return;

    const targetId = replaceTargetId;
    const isWideFace = targetId.endsWith("_wide");
    setIsReplacing(true);
    const toastId = toast.loading("正在替换图片...");
    try {
      await templateApi.replace(realId(targetId), file, isWideFace);
      toast.dismiss(toastId);
      toast.success(isWideFace ? "宽脸图已替换" : "原图已替换（宽脸图需重新生成）");
      await loadTemplates();
    } catch {
      toast.dismiss(toastId);
      toast.error("替换失败，请重试");
    } finally {
      setIsReplacing(false);
      setReplaceTargetId(null);
      e.target.value = "";
    }
  };

  // ---- render ----
  return (
    <MainLayout
      title="模板管理"
      actions={
        <Select value={currentView} onValueChange={(v) => setCurrentView(v as ViewType)}>
          <SelectTrigger className="w-[120px]"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="approved">选用库</SelectItem>
            <SelectItem value="needEdit">待修改库</SelectItem>
            <SelectItem value="recycled">回收库</SelectItem>
          </SelectContent>
        </Select>
      }
    >
      <div className="flex flex-col h-[calc(100vh-140px)]">
        {/* 人群类型横向滚动选择 */}
        <div className="mb-4">
          <ScrollArea className="w-full">
            <div className="flex gap-6 pb-2">
              {crowdTypes.map((type) => (
                <button
                  key={type}
                  onClick={() => setSelectedCrowdType(type)}
                  className={cn(
                    "text-sm whitespace-nowrap transition-colors pb-1 border-b-2",
                    selectedCrowdType === type
                      ? "text-primary border-primary font-medium"
                      : "text-muted-foreground border-transparent hover:text-foreground",
                  )}
                >
                  {type}
                </button>
              ))}
            </div>
            <ScrollBar orientation="horizontal" />
          </ScrollArea>
        </div>

        {/* Loading spinner */}
        {loading ? (
          <div className="flex-1 flex items-center justify-center">
            <Loader2 className="w-8 h-8 animate-spin text-primary" />
          </div>
        ) : (
          <ScrollArea className="flex-1">
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-4 pr-4">
              {currentView === "approved" && filteredTemplates.some(t => t.isWideFace === true) ? (
                filteredTemplates
                  .filter(t => !t.isWideFace)
                  .map((template) => {
                    const pairImage = filteredTemplates.find(t => t.id === template.pairId);
                    return (
                      <div key={template.id} className="flex gap-2">
                        {/* 原版图片 */}
                        <div
                          className="relative aspect-[9/16] rounded-lg overflow-hidden bg-muted border border-border group"
                          onMouseEnter={() => setHoveredId(template.id)}
                          onMouseLeave={() => setHoveredId(null)}
                        >
                          <img src={template.url} alt="原版" className="w-full h-full object-cover" />
                          {hoveredId === template.id && (
                            <div className="absolute inset-0 bg-black/40 flex items-center justify-center cursor-pointer transition-opacity" onClick={() => handleViewLarge(template.url)}>
                              <span className="text-white text-lg font-medium tracking-widest">查看大图</span>
                            </div>
                          )}
                          <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity z-10">
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <Button variant="secondary" size="icon" className="h-7 w-7 bg-white/90 hover:bg-white" onClick={(e) => e.stopPropagation()}>
                                  <MoreHorizontal className="w-4 h-4" />
                                </Button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end">
                                <DropdownMenuItem onClick={() => handleDownload(template.url)}><Download className="w-4 h-4 mr-2" />下载</DropdownMenuItem>
                                <DropdownMenuItem onClick={() => handleReplaceClick(template.id)}><RefreshCw className="w-4 h-4 mr-2" />替换</DropdownMenuItem>
                                <DropdownMenuItem onClick={() => handleMoveToNeedEdit(template.id)}><Edit className="w-4 h-4 mr-2" />待修改</DropdownMenuItem>
                                <DropdownMenuItem onClick={() => handleMoveToRecycle(template.id)}><Trash2 className="w-4 h-4 mr-2" />回收</DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          </div>
                        </div>
                        {/* 宽脸版图片 */}
                        {pairImage && (
                          <div
                            className="relative aspect-[9/16] rounded-lg overflow-hidden bg-muted border border-border group"
                            onMouseEnter={() => setHoveredId(pairImage.id)}
                            onMouseLeave={() => setHoveredId(null)}
                          >
                            <img src={pairImage.url} alt="宽脸版" className="w-full h-full object-cover" />
                            {hoveredId === pairImage.id && (
                              <div className="absolute inset-0 bg-black/40 flex items-center justify-center cursor-pointer transition-opacity" onClick={() => handleViewLarge(pairImage.url)}>
                                <span className="text-white text-lg font-medium tracking-widest">查看大图</span>
                              </div>
                            )}
                            <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity z-10">
                              <DropdownMenu>
                                <DropdownMenuTrigger asChild>
                                  <Button variant="secondary" size="icon" className="h-7 w-7 bg-white/90 hover:bg-white" onClick={(e) => e.stopPropagation()}>
                                    <MoreHorizontal className="w-4 h-4" />
                                  </Button>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="end">
                                  <DropdownMenuItem onClick={() => handleDownload(pairImage.url)}><Download className="w-4 h-4 mr-2" />下载</DropdownMenuItem>
                                  <DropdownMenuItem onClick={() => handleReplaceClick(pairImage.id)}><RefreshCw className="w-4 h-4 mr-2" />替换</DropdownMenuItem>
                                  <DropdownMenuItem onClick={() => handleMoveToRecycle(pairImage.id)}><Trash2 className="w-4 h-4 mr-2" />回收</DropdownMenuItem>
                                </DropdownMenuContent>
                              </DropdownMenu>
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })
              ) : (
                filteredTemplates.map((template) => (
                  <div
                    key={template.id}
                    className="relative aspect-[9/16] rounded-lg overflow-hidden bg-muted border border-border group"
                    onMouseEnter={() => setHoveredId(template.id)}
                    onMouseLeave={() => setHoveredId(null)}
                  >
                    <img src={template.url} alt="" className="w-full h-full object-cover" />
                    {hoveredId === template.id && (
                      <div className="absolute inset-0 bg-black/40 flex items-center justify-center cursor-pointer transition-opacity" onClick={() => handleViewLarge(template.url)}>
                        <span className="text-white text-lg font-medium tracking-widest">查看大图</span>
                      </div>
                    )}
                    <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity z-10">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="secondary" size="icon" className="h-7 w-7 bg-white/90 hover:bg-white" onClick={(e) => e.stopPropagation()}>
                            <MoreHorizontal className="w-4 h-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => handleDownload(template.url)}><Download className="w-4 h-4 mr-2" />下载</DropdownMenuItem>
                          <DropdownMenuItem onClick={() => handleReplaceClick(template.id)}><RefreshCw className="w-4 h-4 mr-2" />替换</DropdownMenuItem>
                          {currentView === "approved" && (
                            <>
                              <DropdownMenuItem onClick={() => handleMoveToNeedEdit(template.id)}><Edit className="w-4 h-4 mr-2" />待修改</DropdownMenuItem>
                              <DropdownMenuItem onClick={() => handleMoveToRecycle(template.id)}><Trash2 className="w-4 h-4 mr-2" />回收</DropdownMenuItem>
                            </>
                          )}
                          {currentView === "needEdit" && (
                            <DropdownMenuItem onClick={() => handleMoveToRecycle(template.id)}><Trash2 className="w-4 h-4 mr-2" />回收</DropdownMenuItem>
                          )}
                          {currentView === "recycled" && (
                            <DropdownMenuItem onClick={() => handleRestore(template.id)}><Eye className="w-4 h-4 mr-2" />恢复</DropdownMenuItem>
                          )}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  </div>
                ))
              )}
            </div>

            {filteredTemplates.length === 0 && (
              <div className="text-center py-16">
                <p className="text-muted-foreground">
                  {currentView === "approved" && `${selectedCrowdType} 分类暂无图片`}
                  {currentView === "needEdit" && `${selectedCrowdType} 分类暂无待修改图片`}
                  {currentView === "recycled" && `${selectedCrowdType} 分类回收库为空`}
                </p>
              </div>
            )}
          </ScrollArea>
        )}

        {/* 右下角按钮 */}
        <div className="fixed bottom-8 right-8">
          {currentView === "needEdit" ? (
            <Button className="bg-primary hover:bg-primary/90 shadow-lg" onClick={handleExportZip} disabled={isExporting}>
              <FileArchive className="w-4 h-4 mr-2" />
              {isExporting ? "正在导出..." : "导出图片"}
            </Button>
          ) : currentView === "recycled" ? (
            <Button className="bg-destructive hover:bg-destructive/90 shadow-lg text-destructive-foreground" onClick={handleClearRecycle}>
              <Eraser className="w-4 h-4 mr-2" />
              清空
            </Button>
          ) : (
            <Button
              className="bg-primary hover:bg-primary/90 shadow-lg"
              onClick={handleUploadClick}
              disabled={isUploading || isReplacing}
            >
              <Upload className="w-4 h-4 mr-2" />
              {isUploading ? "上传中..." : "上传新图片"}
            </Button>
          )}
        </div>

        {/* 隐藏的文件输入 */}
        <input ref={fileInputRef} type="file" accept="image/*" multiple className="hidden" onChange={handleFileUpload} />
        <input ref={replaceInputRef} type="file" accept="image/*" className="hidden" onChange={handleReplaceUpload} />

        {/* 大图预览弹窗 */}
        <Dialog open={!!previewImage} onOpenChange={() => setPreviewImage(null)}>
          <DialogContent className="max-w-4xl p-0 overflow-hidden">
            {previewImage && <img src={previewImage} alt="" className="w-full h-auto max-h-[90vh] object-contain" />}
          </DialogContent>
        </Dialog>
      </div>
    </MainLayout>
  );
}
