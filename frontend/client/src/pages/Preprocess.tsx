import { useState, useCallback, useRef, useEffect } from "react";
import { useLocation } from "wouter";
import { MainLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import {
  Play,
  CheckCircle,
  AlertCircle,
  Loader2,
  ChevronLeft,
  ChevronRight,
  X,
  Droplets,
  Crop,
  Sparkles,
  Eye,
  RotateCcw,
  ArrowRight,
  Move,
  ZoomIn,
} from "lucide-react";
import WatermarkCanvas, { WatermarkCanvasRef } from "@/components/WatermarkCanvas";
import { useUpload } from "@/contexts/UploadContext";
import { uploadApi, preprocessApi, toFileUrl } from "@/lib/api";

// ========== 类型定义 ==========
type ProcessMode = "crop" | "ai-expand";
type ImageStatus = "pending" | "processing" | "success" | "failed";
type WatermarkStatus = "unmarked" | "marked" | "removing" | "done" | "failed";

interface PreprocessImage {
  id: string;
  name: string;
  originalSrc: string;
  processedSrc?: string;
  processMode: ProcessMode;
  status: ImageStatus;
  watermarkStatus: WatermarkStatus;
  maskData?: string;
  hasMask: boolean;
  naturalWidth: number;
  naturalHeight: number;
  aspectRatio: number; // width / height
  // 裁剪偏移量: 0 = 居中, 负值 = 向左/上偏移, 正值 = 向右/下偏移
  // 范围 -1 到 1，表示可移动范围的百分比
  cropOffset: number;
  // AI扩图偏移量: 0 = 居中, 范围 -1 到 1
  expandOffset: number;
}

type Stage = "resize" | "watermark" | "complete";

const TARGET_RATIO = 9 / 16; // 0.5625

// ========== 模拟数据（无批次时的 fallback） ==========

const getImageDimensions = (src: string): Promise<{ width: number; height: number }> =>
  new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => resolve({ width: img.naturalWidth, height: img.naturalHeight });
    img.onerror = () => resolve({ width: 900, height: 1600 });
    img.src = src;
  });

// ========== 可拖拽裁剪预览组件 ==========
function DraggableCropPreview({
  img,
  onOffsetChange,
}: {
  img: PreprocessImage;
  onOffsetChange: (id: string, offset: number) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const isDragging = useRef(false);
  const startPos = useRef(0);
  const startOffset = useRef(0);

  const isWider = img.aspectRatio > TARGET_RATIO;
  const isTaller = img.aspectRatio < TARGET_RATIO;
  const isExact = Math.abs(img.aspectRatio - TARGET_RATIO) < 0.01;

  // 裁剪方向: isWider → 以高度为基准，左右裁剪; isTaller → 以宽度为基准，上下裁剪
  const isHorizontalCrop = isWider;

  // 计算裁剪比例
  let totalCropPercent = 0;
  if (isWider) {
    // 以高度为基准，可见宽度 = 容器宽度 = 高度 * 9/16
    // 原图在容器中以高度填满时，宽度占比 = aspectRatio / TARGET_RATIO
    // 裁掉的比例 = 1 - TARGET_RATIO / aspectRatio
    totalCropPercent = (1 - TARGET_RATIO / img.aspectRatio) * 100;
  } else if (isTaller) {
    // 以宽度为基准，可见高度 = 容器高度 = 宽度 * 16/9
    // 裁掉的比例 = 1 - aspectRatio / TARGET_RATIO
    totalCropPercent = (1 - img.aspectRatio / TARGET_RATIO) * 100;
  }

  // offset 范围 [-1, 1]，0 = 居中
  // 实际蒙版位置
  const halfCrop = totalCropPercent / 2;
  const offsetShift = img.cropOffset * halfCrop;
  // 注意：正 offset 表示裁剪窗口向右/下移动 => 左/上裁剪更多，右/下裁剪更少
  const side1 = Math.max(0, halfCrop + offsetShift); // 左/上
  const side2 = Math.max(0, halfCrop - offsetShift); // 右/下

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (isExact) return;
      e.preventDefault();
      isDragging.current = true;
      startPos.current = isHorizontalCrop ? e.clientX : e.clientY;
      startOffset.current = img.cropOffset;

      const handleMouseMove = (ev: MouseEvent) => {
        if (!isDragging.current || !containerRef.current) return;
        const rect = containerRef.current.getBoundingClientRect();
        const size = isHorizontalCrop ? rect.width : rect.height;
        const delta = isHorizontalCrop
          ? ev.clientX - startPos.current
          : ev.clientY - startPos.current;
        // 将像素位移转换为 offset 值
        const sensitivity = 3; // 灵敏度倍数
        const newOffset = Math.max(-1, Math.min(1, startOffset.current + (delta / size) * sensitivity));
        onOffsetChange(img.id, newOffset);
      };

      const handleMouseUp = () => {
        isDragging.current = false;
        window.removeEventListener("mousemove", handleMouseMove);
        window.removeEventListener("mouseup", handleMouseUp);
      };

      window.addEventListener("mousemove", handleMouseMove);
      window.addEventListener("mouseup", handleMouseUp);
    },
    [img.id, img.cropOffset, isHorizontalCrop, isExact, onOffsetChange]
  );

  if (isExact) {
    return (
      <div className="relative w-full h-full overflow-hidden">
        <img src={img.originalSrc} alt={img.name} className="w-full h-full object-cover" />
        <div className="absolute top-2 left-2 z-20">
          <Badge className="bg-green-500 text-white text-xs shadow">
            <CheckCircle className="w-3 h-3 mr-1" />
            已符合9:16
          </Badge>
        </div>
      </div>
    );
  }

  if (isHorizontalCrop) {
    // 偏宽图片 → 以高度为基准，居中左右裁剪
    return (
      <div
        ref={containerRef}
        className="relative w-full h-full overflow-hidden cursor-ew-resize select-none"
        onMouseDown={handleMouseDown}
      >
        <img src={img.originalSrc} alt={img.name} className="w-full h-full object-cover" />
        {/* 左侧裁剪蒙版 */}
        <div
          className="absolute top-0 left-0 bottom-0 bg-black/50 z-10 pointer-events-none transition-[width] duration-75"
          style={{ width: `${side1}%` }}
        >
          {side1 > 5 && (
            <div className="absolute inset-0 flex items-center justify-center">
              <span className="text-white/70 text-[10px] font-medium rotate-[-90deg] whitespace-nowrap">裁剪</span>
            </div>
          )}
        </div>
        {/* 右侧裁剪蒙版 */}
        <div
          className="absolute top-0 right-0 bottom-0 bg-black/50 z-10 pointer-events-none transition-[width] duration-75"
          style={{ width: `${side2}%` }}
        >
          {side2 > 5 && (
            <div className="absolute inset-0 flex items-center justify-center">
              <span className="text-white/70 text-[10px] font-medium rotate-[90deg] whitespace-nowrap">裁剪</span>
            </div>
          )}
        </div>
        {/* 保留区域边框 */}
        <div
          className="absolute top-0 bottom-0 z-10 pointer-events-none border-2 border-dashed border-white/80 transition-all duration-75"
          style={{ left: `${side1}%`, right: `${side2}%` }}
        />
        {/* 标签 */}
        <div className="absolute top-2 left-2 z-20">
          <Badge className="bg-orange-500 text-white text-xs shadow">
            <Crop className="w-3 h-3 mr-1" />
            左右裁剪
          </Badge>
        </div>
        {/* 拖拽提示 */}
        <div className="absolute bottom-2 left-1/2 -translate-x-1/2 z-20">
          <Badge variant="outline" className="bg-black/40 text-white/90 border-white/30 text-[10px] backdrop-blur-sm">
            <Move className="w-3 h-3 mr-1" />
            左右拖拽调整
          </Badge>
        </div>
      </div>
    );
  }

  // 偏高图片 → 以宽度为基准，居中上下裁剪
  return (
    <div
      ref={containerRef}
      className="relative w-full h-full overflow-hidden cursor-ns-resize select-none"
      onMouseDown={handleMouseDown}
    >
      <img src={img.originalSrc} alt={img.name} className="w-full h-full object-cover" />
      {/* 顶部裁剪蒙版 */}
      <div
        className="absolute top-0 left-0 right-0 bg-black/50 z-10 pointer-events-none transition-[height] duration-75"
        style={{ height: `${side1}%` }}
      >
        {side1 > 5 && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-white/70 text-[10px] font-medium whitespace-nowrap">裁剪</span>
          </div>
        )}
      </div>
      {/* 底部裁剪蒙版 */}
      <div
        className="absolute bottom-0 left-0 right-0 bg-black/50 z-10 pointer-events-none transition-[height] duration-75"
        style={{ height: `${side2}%` }}
      >
        {side2 > 5 && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-white/70 text-[10px] font-medium whitespace-nowrap">裁剪</span>
          </div>
        )}
      </div>
      {/* 保留区域边框 */}
      <div
        className="absolute left-0 right-0 z-10 pointer-events-none border-2 border-dashed border-white/80 transition-all duration-75"
        style={{ top: `${side1}%`, bottom: `${side2}%` }}
      />
      {/* 标签 */}
      <div className="absolute top-2 left-2 z-20">
        <Badge className="bg-orange-500 text-white text-xs shadow">
          <Crop className="w-3 h-3 mr-1" />
          上下裁剪
        </Badge>
      </div>
      {/* 拖拽提示 */}
      <div className="absolute bottom-2 left-1/2 -translate-x-1/2 z-20">
        <Badge variant="outline" className="bg-black/40 text-white/90 border-white/30 text-[10px] backdrop-blur-sm">
          <Move className="w-3 h-3 mr-1" />
          上下拖拽调整
        </Badge>
      </div>
    </div>
  );
}

// ========== 可拖拽AI扩图预览组件 ==========
function DraggableExpandPreview({
  img,
  onOffsetChange,
}: {
  img: PreprocessImage;
  onOffsetChange: (id: string, offset: number) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const isDragging = useRef(false);
  const startPos = useRef(0);
  const startOffset = useRef(0);

  const isWider = img.aspectRatio > TARGET_RATIO;
  const isExact = Math.abs(img.aspectRatio - TARGET_RATIO) < 0.01;

  // AI扩图方向:
  // isWider → 以宽度为基准，居中上下扩展（垂直方向拖拽）
  // isTaller → 以高度为基准，居中左右扩展（水平方向拖拽）
  const isVerticalExpand = isWider;

  // 计算原图在9:16画布中占据的比例
  let imgSizePercent = 100;
  if (isWider) {
    // 以宽度为基准: 原图宽度填满，高度 = width / aspectRatio
    // 9:16画布高度 = width * 16/9
    // 原图高度占比 = (width/aspectRatio) / (width*16/9) = 9/(16*aspectRatio)
    imgSizePercent = (9 / (16 * img.aspectRatio)) * 100;
  } else if (!isExact) {
    // 以高度为基准: 原图高度填满，宽度 = height * aspectRatio
    // 9:16画布宽度 = height * 9/16
    // 原图宽度占比 = (height*aspectRatio) / (height*9/16) = aspectRatio*16/9
    imgSizePercent = (img.aspectRatio * 16 / 9) * 100;
  }

  const expandTotal = 100 - imgSizePercent; // 总扩展空间
  const halfExpand = expandTotal / 2;
  const offsetShift = img.expandOffset * halfExpand;
  const expandSide1 = Math.max(0, halfExpand + offsetShift); // 上/左 扩展
  const expandSide2 = Math.max(0, halfExpand - offsetShift); // 下/右 扩展

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (isExact) return;
      e.preventDefault();
      isDragging.current = true;
      startPos.current = isVerticalExpand ? e.clientY : e.clientX;
      startOffset.current = img.expandOffset;

      const handleMouseMove = (ev: MouseEvent) => {
        if (!isDragging.current || !containerRef.current) return;
        const rect = containerRef.current.getBoundingClientRect();
        const size = isVerticalExpand ? rect.height : rect.width;
        const delta = isVerticalExpand
          ? ev.clientY - startPos.current
          : ev.clientX - startPos.current;
        const sensitivity = 3;
        const newOffset = Math.max(-1, Math.min(1, startOffset.current + (delta / size) * sensitivity));
        onOffsetChange(img.id, newOffset);
      };

      const handleMouseUp = () => {
        isDragging.current = false;
        window.removeEventListener("mousemove", handleMouseMove);
        window.removeEventListener("mouseup", handleMouseUp);
      };

      window.addEventListener("mousemove", handleMouseMove);
      window.addEventListener("mouseup", handleMouseUp);
    },
    [img.id, img.expandOffset, isVerticalExpand, isExact, onOffsetChange]
  );

  if (isExact) {
    return (
      <div className="relative w-full h-full overflow-hidden">
        <img src={img.originalSrc} alt={img.name} className="w-full h-full object-cover" />
        <div className="absolute top-2 left-2 z-20">
          <Badge className="bg-green-500 text-white text-xs shadow">
            <CheckCircle className="w-3 h-3 mr-1" />
            已符合9:16
          </Badge>
        </div>
      </div>
    );
  }

  if (isVerticalExpand) {
    // 偏宽 → 以宽度为基准，上下扩展
    return (
      <div
        ref={containerRef}
        className={`relative w-full h-full overflow-hidden select-none ${isExact ? "" : "cursor-ns-resize"}`}
        onMouseDown={handleMouseDown}
      >
        {/* 9:16 画布背景 */}
        <div className="absolute inset-0 bg-gray-100" />
        {/* 上方扩展区域 */}
        <div
          className="absolute top-0 left-0 right-0 z-10 pointer-events-none transition-[height] duration-75"
          style={{ height: `${expandSide1}%` }}
        >
          <div
            className="w-full h-full border-b-2 border-dashed border-blue-400"
            style={{
              backgroundImage: `repeating-linear-gradient(45deg, transparent, transparent 6px, rgba(59,130,246,0.08) 6px, rgba(59,130,246,0.08) 12px)`,
            }}
          >
            {expandSide1 > 8 && (
              <div className="absolute inset-0 flex items-center justify-center">
                <span className="text-blue-400 text-[10px] font-medium">↑ 扩展 {Math.round(expandSide1)}%</span>
              </div>
            )}
          </div>
        </div>
        {/* 原图区域 */}
        <div
          className="absolute left-0 right-0 z-0 overflow-hidden transition-all duration-75"
          style={{ top: `${expandSide1}%`, bottom: `${expandSide2}%` }}
        >
          <img
            src={img.originalSrc}
            alt={img.name}
            className="w-full h-full object-cover"
          />
        </div>
        {/* 下方扩展区域 */}
        <div
          className="absolute bottom-0 left-0 right-0 z-10 pointer-events-none transition-[height] duration-75"
          style={{ height: `${expandSide2}%` }}
        >
          <div
            className="w-full h-full border-t-2 border-dashed border-blue-400"
            style={{
              backgroundImage: `repeating-linear-gradient(45deg, transparent, transparent 6px, rgba(59,130,246,0.08) 6px, rgba(59,130,246,0.08) 12px)`,
            }}
          >
            {expandSide2 > 8 && (
              <div className="absolute inset-0 flex items-center justify-center">
                <span className="text-blue-400 text-[10px] font-medium">↓ 扩展 {Math.round(expandSide2)}%</span>
              </div>
            )}
          </div>
        </div>
        {/* 标签 */}
        <div className="absolute top-2 left-2 z-20">
          <Badge className="bg-blue-500 text-white text-xs shadow">
            <Sparkles className="w-3 h-3 mr-1" />
            上下扩展
          </Badge>
        </div>
        {/* 拖拽提示 */}
        <div className="absolute bottom-2 left-1/2 -translate-x-1/2 z-20">
          <Badge variant="outline" className="bg-black/40 text-white/90 border-white/30 text-[10px] backdrop-blur-sm">
            <Move className="w-3 h-3 mr-1" />
            上下拖拽调整比例
          </Badge>
        </div>
      </div>
    );
  }

  // 偏高 → 以高度为基准，左右扩展
  return (
    <div
      ref={containerRef}
      className={`relative w-full h-full overflow-hidden select-none ${isExact ? "" : "cursor-ew-resize"}`}
      onMouseDown={handleMouseDown}
    >
      {/* 9:16 画布背景 */}
      <div className="absolute inset-0 bg-gray-100" />
      {/* 左侧扩展区域 */}
      <div
        className="absolute top-0 left-0 bottom-0 z-10 pointer-events-none transition-[width] duration-75"
        style={{ width: `${expandSide1}%` }}
      >
        <div
          className="w-full h-full border-r-2 border-dashed border-blue-400"
          style={{
            backgroundImage: `repeating-linear-gradient(45deg, transparent, transparent 6px, rgba(59,130,246,0.08) 6px, rgba(59,130,246,0.08) 12px)`,
          }}
        >
          {expandSide1 > 8 && (
            <div className="absolute inset-0 flex items-center justify-center">
              <span className="text-blue-400 text-[10px] font-medium rotate-[-90deg] whitespace-nowrap">← {Math.round(expandSide1)}%</span>
            </div>
          )}
        </div>
      </div>
      {/* 原图区域 */}
      <div
        className="absolute top-0 bottom-0 z-0 overflow-hidden transition-all duration-75"
        style={{ left: `${expandSide1}%`, right: `${expandSide2}%` }}
      >
        <img
          src={img.originalSrc}
          alt={img.name}
          className="w-full h-full object-cover"
        />
      </div>
      {/* 右侧扩展区域 */}
      <div
        className="absolute top-0 right-0 bottom-0 z-10 pointer-events-none transition-[width] duration-75"
        style={{ width: `${expandSide2}%` }}
      >
        <div
          className="w-full h-full border-l-2 border-dashed border-blue-400"
          style={{
            backgroundImage: `repeating-linear-gradient(45deg, transparent, transparent 6px, rgba(59,130,246,0.08) 6px, rgba(59,130,246,0.08) 12px)`,
          }}
        >
          {expandSide2 > 8 && (
            <div className="absolute inset-0 flex items-center justify-center">
              <span className="text-blue-400 text-[10px] font-medium rotate-[90deg] whitespace-nowrap">{Math.round(expandSide2)}% →</span>
            </div>
          )}
        </div>
      </div>
      {/* 标签 */}
      <div className="absolute top-2 left-2 z-20">
        <Badge className="bg-blue-500 text-white text-xs shadow">
          <Sparkles className="w-3 h-3 mr-1" />
          左右扩展
        </Badge>
      </div>
      {/* 拖拽提示 */}
      <div className="absolute bottom-2 left-1/2 -translate-x-1/2 z-20">
        <Badge variant="outline" className="bg-black/40 text-white/90 border-white/30 text-[10px] backdrop-blur-sm">
          <Move className="w-3 h-3 mr-1" />
          左右拖拽调整比例
        </Badge>
      </div>
    </div>
  );
}

// ========== 主组件 ==========
export default function Preprocess() {
  const [, setLocation] = useLocation();
  const { batchId, uploadedFiles } = useUpload();
  const [images, setImages] = useState<PreprocessImage[]>([]);
  const [stage, setStage] = useState<Stage>("resize");
  const [isProcessing, setIsProcessing] = useState(false);
  const [processProgress, setProcessProgress] = useState({ current: 0, total: 0 });
  const [imagesLoaded, setImagesLoaded] = useState(false);
  /** 数据来源标记：api = 后端批次, context = UploadContext blob, mock = 示例 */
  const [dataSource, setDataSource] = useState<"api" | "context" | "mock">("mock");

  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewIndex, setPreviewIndex] = useState(0);
  const canvasRef = useRef<WatermarkCanvasRef>(null);

  const [isRemovingWatermark, setIsRemovingWatermark] = useState(false);
  const [removeProgress, setRemoveProgress] = useState({ current: 0, total: 0 });

  // 纯大图预览（所有阶段可用）
  const [simplePreviewOpen, setSimplePreviewOpen] = useState(false);
  const [simplePreviewIndex, setSimplePreviewIndex] = useState(0);

  // resize 阶段是否已完成处理（用于原地展示结果，不自动跳转）
  const [resizeDone, setResizeDone] = useState(false);
  // watermark 阶段是否已完成（用于原地展示结果，不自动跳转）
  const [watermarkDone, setWatermarkDone] = useState(false);

  // ========== 初始化：优先从后端加载批次图片 ==========
  useEffect(() => {
    let cancelled = false;

    // 后端状态 → 前端 ImageStatus 映射
    const mapStatus = (backendStatus?: string): ImageStatus => {
      if (!backendStatus) return "pending";
      switch (backendStatus) {
        case "completed": return "success";
        case "failed": return "failed";
        case "processing": return "processing";
        default: return "pending";
      }
    };

    // 后端 preprocess_mode → 前端 ProcessMode 映射
    const mapMode = (mode?: string): ProcessMode => {
      if (mode === "expand" || mode === "ai-expand") return "ai-expand";
      return "crop";
    };

    const loadImages = async () => {
      // 用于存储从后端获取的完整图片信息（含处理结果）
      type RichSourceImage = {
        id: string;
        name: string;
        originalSrc: string;
        processedSrc?: string;
        status: ImageStatus;
        processMode: ProcessMode;
        watermarkStatus: WatermarkStatus;
      };

      let richImages: RichSourceImage[] = [];
      let source: "api" | "context" | "mock" = "mock";

      // 1) 优先：有 batchId → 从后端获取真实图片（含处理结果）
      if (batchId) {
        try {
          const detail = await uploadApi.getBatch(batchId);
          if (!cancelled && detail?.images?.length) {
            richImages = detail.images
              .filter((img: any) => img.original_path)
              .map((img: any) => ({
                id: img.id,
                name: img.filename,
                originalSrc: toFileUrl(img.original_path),
                processedSrc: img.processed_path ? toFileUrl(img.processed_path) : undefined,
                status: mapStatus(img.status),
                processMode: mapMode(img.preprocess_mode),
                watermarkStatus: img.watermark_removed ? "done" as WatermarkStatus : "unmarked" as WatermarkStatus,
              }));
            source = "api";
          }
        } catch {
          // 后端不可达，降级到 context
        }
      }

      // 2) 降级：UploadContext 中有 blob URL
      if (richImages.length === 0 && uploadedFiles.length > 0) {
        richImages = uploadedFiles.map((f) => ({
          id: f.id,
          name: f.name,
          originalSrc: f.preview,
          status: "pending" as ImageStatus,
          processMode: "crop" as ProcessMode,
          watermarkStatus: "unmarked" as WatermarkStatus,
        }));
        source = "context";
      }

      // 3) 无数据
      if (richImages.length === 0) {
        source = "mock";
      }

      if (cancelled) return;

      const preprocessImages: PreprocessImage[] = await Promise.all(
        richImages.map(async (src) => {
          // 优先用处理后的图获取尺寸（展示用），否则用原图
          const displaySrc = src.processedSrc || src.originalSrc;
          const dims = await getImageDimensions(displaySrc);
          return {
            id: src.id,
            name: src.name,
            originalSrc: src.originalSrc,
            processedSrc: src.processedSrc,
            processMode: src.processMode,
            status: src.status,
            watermarkStatus: src.watermarkStatus,
            hasMask: false,
            naturalWidth: dims.width,
            naturalHeight: dims.height,
            aspectRatio: dims.width / dims.height,
            cropOffset: 0,
            expandOffset: 0,
          };
        })
      );

      if (!cancelled) {
        setImages(preprocessImages);
        setDataSource(source);
        setImagesLoaded(true);

        // 如果后端已有处理完成的图片，自动恢复到正确的阶段
        if (source === "api") {
          const hasProcessed = preprocessImages.some((img) => img.status === "success" && img.processedSrc);
          const someWatermarkDone = preprocessImages.some((img) => img.status === "success" && img.watermarkStatus === "done");
          const allWatermarkDone = preprocessImages.length > 0 && preprocessImages
            .filter((img) => img.status === "success")
            .every((img) => img.watermarkStatus === "done");

          if (hasProcessed) {
            // resize 已完成，进入去水印阶段
            setResizeDone(true);
            setStage("watermark");
            if (allWatermarkDone && someWatermarkDone) {
              setWatermarkDone(true);
            }
          }
        }
      }
    };

    loadImages();
    return () => { cancelled = true; };
  }, [batchId, uploadedFiles]);

  // ========== 处理模式和偏移量 ==========
  const handleChangeMode = useCallback((id: string, mode: ProcessMode) => {
    setImages((prev) =>
      prev.map((img) => (img.id === id ? { ...img, processMode: mode } : img))
    );
  }, []);

  const handleCropOffsetChange = useCallback((id: string, offset: number) => {
    setImages((prev) =>
      prev.map((img) => (img.id === id ? { ...img, cropOffset: offset } : img))
    );
  }, []);

  const handleExpandOffsetChange = useCallback((id: string, offset: number) => {
    setImages((prev) =>
      prev.map((img) => (img.id === id ? { ...img, expandOffset: offset } : img))
    );
  }, []);

  // ========== 阶段一：尺寸标准化 ==========

  /** 轮询后端预处理进度，更新前端状态 */
  const pollProgress = useCallback(
    async (bid: string) => {
      const POLL_INTERVAL = 1500;
      const MAX_POLLS = 200; // 最多轮询 5 分钟
      let polls = 0;

      while (polls < MAX_POLLS) {
        await new Promise((r) => setTimeout(r, POLL_INTERVAL));
        polls++;

        const info = await preprocessApi.progress(bid);
        if (!info) break;

        setProcessProgress({ current: info.completed + info.failed, total: info.total });

        if (info.status === "completed" || info.status === "error") {
          // 完成后重新拉取批次详情，刷新每张图片的状态
          const detail = await uploadApi.getBatch(bid);
          if (detail?.images) {
            setImages((prev) =>
              prev.map((img) => {
                const remote = detail.images.find((r) => r.id === img.id);
                if (!remote) return img;
                const newStatus: ImageStatus =
                  remote.status === "completed" ? "success" :
                  remote.status === "failed" || remote.status === "discarded" ? "failed" : img.status;
                return {
                  ...img,
                  status: newStatus,
                  processedSrc: remote.processed_path ? toFileUrl(remote.processed_path) : undefined,
                  watermarkStatus: remote.watermark_removed ? "done" : img.watermarkStatus,
                  hasMask: remote.watermark_removed ? false : img.hasMask,
                  maskData: remote.watermark_removed ? undefined : img.maskData,
                };
              })
            );
          }
          return info;
        }
      }
      return null;
    },
    [],
  );

  // ========== 刷新恢复：检查是否有正在运行的预处理任务 ==========
  useEffect(() => {
    if (!batchId || dataSource !== "api") return;
    let cancelled = false;
    (async () => {
      try {
        const info = await preprocessApi.progress(batchId);
        if (cancelled || !info || info.status !== "running") return;

        setIsProcessing(true);
        setProcessProgress({ current: info.completed + info.failed, total: info.total });

        const finalInfo = await pollProgress(batchId);
        if (cancelled) return;
        setIsProcessing(false);

        if (finalInfo?.status === "completed") {
          setResizeDone(true);
          toast.success(`尺寸标准化完成！成功 ${finalInfo.completed}，失败 ${finalInfo.failed}`);
        }
      } catch { /* ignore */ }
    })();
    return () => { cancelled = true; };
  }, [batchId, dataSource, pollProgress]);

  /** 模拟处理（无后端时的 fallback） */
  const simulateSingle = useCallback(
    () => new Promise<boolean>((resolve) => setTimeout(() => resolve(Math.random() > 0.1), 800)),
    []
  );

  /** 客户端 9:16 裁剪 (offscreen canvas) */
  const cropImageClientSide = useCallback(
    (src: string, naturalWidth: number, naturalHeight: number, cropOffset: number): Promise<string> => {
      return new Promise((resolve, reject) => {
        const img = new Image();
        img.crossOrigin = "anonymous";
        img.onload = () => {
          const targetRatio = 9 / 16;
          const currentRatio = naturalWidth / naturalHeight;
          let sx = 0, sy = 0, sw = naturalWidth, sh = naturalHeight;

          if (currentRatio > targetRatio) {
            // 图片比 9:16 更宽 — 裁剪宽度
            sw = Math.round(naturalHeight * targetRatio);
            const maxOffset = naturalWidth - sw;
            sx = Math.round((naturalWidth - sw) / 2 + cropOffset * (maxOffset / 2));
            sx = Math.max(0, Math.min(sx, maxOffset));
          } else if (currentRatio < targetRatio) {
            // 图片比 9:16 更高 — 裁剪高度
            sh = Math.round(naturalWidth / targetRatio);
            const maxOffset = naturalHeight - sh;
            sy = Math.round((naturalHeight - sh) / 2 + cropOffset * (maxOffset / 2));
            sy = Math.max(0, Math.min(sy, maxOffset));
          }

          const canvas = document.createElement("canvas");
          canvas.width = sw;
          canvas.height = sh;
          const ctx = canvas.getContext("2d");
          if (!ctx) { reject(new Error("Canvas context unavailable")); return; }
          ctx.drawImage(img, sx, sy, sw, sh, 0, 0, sw, sh);
          canvas.toBlob(
            (blob) => blob ? resolve(URL.createObjectURL(blob)) : reject(new Error("toBlob failed")),
            "image/jpeg", 0.92
          );
        };
        img.onerror = () => reject(new Error("Image load failed"));
        img.src = src;
      });
    },
    []
  );

  const handleStartResize = useCallback(async () => {
    // 在 setImages 之前预计算待处理图片 ID，避免闭包 stale 引用
    const toProcess = images.filter((i) => i.status === "pending" || i.status === "failed");
    const toProcessIds = toProcess.map((i) => i.id);
    const total = toProcessIds.length;

    if (total === 0) {
      toast.info("没有待处理的图片");
      return;
    }

    setIsProcessing(true);
    setProcessProgress({ current: 0, total });

    // 标记所有待处理图片为 processing
    setImages((prev) =>
      prev.map((item) =>
        item.status === "pending" || item.status === "failed"
          ? { ...item, status: "processing" }
          : item
      )
    );

    if (dataSource === "api" && batchId) {
      // ---- 真实后端调用 ----
      // 为每张待处理图片显式指定模式，不依赖批次默认值
      const cropOffsets: Record<string, number> = {};
      const expandOffsets: Record<string, number> = {};
      const imageModes: Record<string, string> = {};
      toProcess.forEach((img) => {
        imageModes[img.id] = img.processMode === "ai-expand" ? "expand" : "crop";
        if (img.processMode === "ai-expand") {
          if (img.expandOffset !== 0) expandOffsets[img.id] = img.expandOffset;
        } else if (img.cropOffset !== 0) {
          cropOffsets[img.id] = img.cropOffset;
        }
      });
      // 批次默认 mode 仅作 fallback，取多数模式
      const expandCount = toProcess.filter((i) => i.processMode === "ai-expand").length;
      const mode = expandCount > toProcess.length / 2 ? "expand" : "crop";
      const result = await preprocessApi.start(
        batchId,
        mode as "crop" | "expand",
        cropOffsets,
        imageModes,
        expandOffsets,
      );
      if (!result) {
        // 启动失败，恢复状态
        setImages((prev) =>
          prev.map((item) =>
            item.status === "processing" ? { ...item, status: "pending" } : item
          )
        );
        setIsProcessing(false);
        return;
      }
      const pollResult = await pollProgress(batchId);

      // 后端处理出错时提示用户
      if (pollResult && pollResult.status === "error") {
        const logs: string[] = pollResult.logs || [];
        const lastLog = logs.filter((l: string) => l.includes("[ERROR]") || l.includes("[WARN]")).pop();
        toast.error("处理过程中出现错误", {
          description: lastLog || "请检查后端日志",
          duration: 6000,
        });
        // 将仍处于 processing 的图片标记为 failed
        setImages((prev) =>
          prev.map((item) =>
            item.status === "processing" ? { ...item, status: "failed" } : item
          )
        );
        setIsProcessing(false);
        return;
      }

      // 轮询超时或网络错误时兜底刷新状态
      if (!pollResult) {
        try {
          const detail = await uploadApi.getBatch(batchId);
          if (detail?.images) {
            setImages((prev) =>
              prev.map((img) => {
                const remote = detail.images.find((r: { id: string }) => r.id === img.id);
                if (!remote) return img;
                const newStatus: ImageStatus =
                  remote.status === "completed" ? "success" :
                  remote.status === "failed" || remote.status === "discarded" ? "failed" :
                  img.status === "processing" ? "failed" : img.status;
                return {
                  ...img,
                  status: newStatus,
                  processedSrc: remote.processed_path ? toFileUrl(remote.processed_path) : undefined,
                };
              })
            );
          } else {
            setImages((prev) =>
              prev.map((item) =>
                item.status === "processing" ? { ...item, status: "failed" } : item
              )
            );
          }
        } catch {
          setImages((prev) =>
            prev.map((item) =>
              item.status === "processing" ? { ...item, status: "failed" } : item
            )
          );
        }
      }
    } else {
      // ---- context: 客户端裁剪 / mock: 模拟 ----
      for (let i = 0; i < toProcessIds.length; i++) {
        const imgId = toProcessIds[i];
        try {
          const imgData = toProcess.find((img) => img.id === imgId);
          let processedSrc: string | undefined;

          if (dataSource === "context" && imgData) {
            processedSrc = await cropImageClientSide(
              imgData.originalSrc, imgData.naturalWidth, imgData.naturalHeight, imgData.cropOffset
            );
          } else {
            const success = await simulateSingle();
            if (!success) {
              setImages((prev) => prev.map((item) =>
                item.id === imgId ? { ...item, status: "failed" } : item
              ));
              setProcessProgress({ current: i + 1, total });
              continue;
            }
          }

          setImages((prev) => prev.map((item) =>
            item.id === imgId
              ? { ...item, status: "success", processedSrc: processedSrc || item.originalSrc }
              : item
          ));
        } catch (err) {
          console.error("Client-side crop failed:", imgId, err);
          setImages((prev) => prev.map((item) =>
            item.id === imgId ? { ...item, status: "failed" } : item
          ));
        }
        setProcessProgress({ current: i + 1, total });
      }
    }

    setIsProcessing(false);

    // 根据实际结果显示不同提示
    setImages((prev) => {
      const successes = prev.filter((i) => i.status === "success").length;
      const failures = prev.filter((i) => i.status === "failed").length;
      if (failures > 0 && successes > 0) {
        toast.warning(`处理完成：${successes} 张成功，${failures} 张失败`, {
          description: "可重试失败项或点击「进入去水印」继续",
        });
      } else if (failures > 0 && successes === 0) {
        toast.error("全部处理失败", { description: "请检查后端服务状态" });
      } else {
        toast.success("尺寸标准化完成！", { description: "点击图片可查看大图，确认后进入去水印" });
      }
      return prev; // 不修改 state，只是借用最新值
    });
    // 保持在当前阶段，用户手动点击“进入去水印”
    setResizeDone(true);
  }, [images, dataSource, batchId, pollProgress, simulateSingle, cropImageClientSide]);

  const handleRetryResize = useCallback(async (id: string) => {
    setImages((prev) =>
      prev.map((item) =>
        item.id === id ? { ...item, status: "processing" } : item
      )
    );

    if (dataSource === "api") {
      // 真实后端重试
      const result = await preprocessApi.retry(id);
      // 重新拉取该图片状态
      if (batchId) {
        const detail = await uploadApi.getBatch(batchId);
        const remote = detail?.images?.find((r) => r.id === id);
        if (remote) {
          const newStatus: ImageStatus =
            remote.status === "completed" ? "success" :
            remote.status === "failed" || remote.status === "discarded" ? "failed" : "pending";
          setImages((prev) =>
            prev.map((item) =>
              item.id === id
                ? { ...item, status: newStatus, processedSrc: remote.processed_path ? toFileUrl(remote.processed_path) : undefined }
                : item
            )
          );
          toast[newStatus === "success" ? "success" : "error"](
            newStatus === "success" ? "重试成功" : (result ? "重试失败，请再次尝试" : "重试请求失败")
          );
          return;
        }
      }
    }

    // Mock fallback
    const success = await simulateSingle();
    setImages((prev) =>
      prev.map((item) =>
        item.id === id
          ? { ...item, status: success ? "success" : "failed", processedSrc: success ? item.originalSrc : undefined }
          : item
      )
    );
    toast[success ? "success" : "error"](success ? "重试成功" : "重试失败，请再次尝试");
  }, [dataSource, batchId, simulateSingle]);

  // ========== 阶段二：去水印涂抹 ==========
  const handleOpenPreview = useCallback(
    (index: number) => {
      const successImgs = images.filter((img) => img.status === "success");
      if (successImgs.length === 0) return;
      const successIndex = successImgs.findIndex((img) => img.id === images[index].id);
      if (successIndex === -1) return;
      setPreviewIndex(successIndex);
      setPreviewOpen(true);
    },
    [images]
  );

  const successImages = images.filter((img) => img.status === "success");
  const getMaskStatusAfterUpdate = useCallback(
    (currentStatus: WatermarkStatus, hasMask: boolean): WatermarkStatus => {
      if (hasMask) return "marked";
      if (currentStatus === "done" || currentStatus === "failed") return currentStatus;
      return "unmarked";
    },
    []
  );
  const withCacheBust = useCallback((url: string) => {
    if (!url) return url;
    const sep = url.includes("?") ? "&" : "?";
    return `${url}${sep}t=${Date.now()}`;
  }, []);

  const saveMaskAndNavigate = useCallback(
    (newIndex: number) => {
      if (canvasRef.current) {
        const maskData = canvasRef.current.getMaskData();
        const hasMask = canvasRef.current.hasMask();
        const currentImg = successImages[previewIndex];
        if (!currentImg) return;
        setImages((prev) =>
          prev.map((img) =>
            img.id === currentImg.id
              ? {
                ...img,
                maskData: hasMask ? maskData : undefined,
                hasMask,
                watermarkStatus: getMaskStatusAfterUpdate(currentImg.watermarkStatus, hasMask),
              }
              : img
          )
        );
        if (hasMask) setWatermarkDone(false);
      }
      setPreviewIndex(newIndex);
    },
    [previewIndex, successImages, getMaskStatusAfterUpdate]
  );

  const handlePrevImage = useCallback(() => {
    saveMaskAndNavigate(previewIndex > 0 ? previewIndex - 1 : successImages.length - 1);
  }, [saveMaskAndNavigate, previewIndex, successImages.length]);

  const handleNextImage = useCallback(() => {
    saveMaskAndNavigate(previewIndex < successImages.length - 1 ? previewIndex + 1 : 0);
  }, [saveMaskAndNavigate, previewIndex, successImages.length]);

  const handleClosePreview = useCallback(() => {
    if (canvasRef.current) {
      const maskData = canvasRef.current.getMaskData();
      const hasMask = canvasRef.current.hasMask();
      const currentImg = successImages[previewIndex];
      if (!currentImg) return;
      setImages((prev) =>
        prev.map((img) =>
          img.id === currentImg.id
            ? {
              ...img,
              maskData: hasMask ? maskData : undefined,
              hasMask,
              watermarkStatus: getMaskStatusAfterUpdate(currentImg.watermarkStatus, hasMask),
            }
            : img
        )
      );
      if (hasMask) setWatermarkDone(false);
    }
    setPreviewOpen(false);
  }, [previewIndex, successImages, getMaskStatusAfterUpdate]);

  const handleMaskChange = useCallback(
    (maskData: string, hasMask: boolean) => {
      const currentImg = successImages[previewIndex];
      if (!currentImg) return;
      setImages((prev) =>
        prev.map((img) =>
          img.id === currentImg.id
            ? {
              ...img,
              maskData: hasMask ? maskData : undefined,
              hasMask,
              watermarkStatus: hasMask ? "marked" : getMaskStatusAfterUpdate(currentImg.watermarkStatus, false),
            }
            : img
        )
      );
      if (hasMask) setWatermarkDone(false);
    },
    [previewIndex, successImages, getMaskStatusAfterUpdate]
  );

  const syncCurrentMask = useCallback(() => {
    const currentImg = successImages[previewIndex];
    if (!currentImg || !canvasRef.current) return currentImg;
    const maskData = canvasRef.current.getMaskData();
    const hasMask = canvasRef.current.hasMask();
    setImages((prev) =>
      prev.map((img) =>
        img.id === currentImg.id
          ? {
            ...img,
            maskData: hasMask ? maskData : undefined,
            hasMask,
            watermarkStatus: getMaskStatusAfterUpdate(currentImg.watermarkStatus, hasMask),
          }
          : img
      )
    );
    if (hasMask) setWatermarkDone(false);
    return { ...currentImg, maskData, hasMask };
  }, [previewIndex, successImages, getMaskStatusAfterUpdate]);

  // ========== 阶段三：一键去水印 ==========
  const markedImages = images.filter(
    (img) => img.status === "success" && img.watermarkStatus === "marked" && img.hasMask && !!img.maskData
  );
  const hasUnresolvedWatermark = (list: PreprocessImage[]) =>
    list.some(
      (img) =>
        img.status === "success"
        && (img.watermarkStatus === "marked" || img.watermarkStatus === "failed" || img.watermarkStatus === "removing")
    );

  const handleRemoveWatermarks = useCallback(async () => {
    if (previewOpen) {
      syncCurrentMask();
      setPreviewOpen(false);
    }
    if (markedImages.length === 0) {
      toast.info("没有需要去水印的图片，可直接进入提示词页面");
      setWatermarkDone(true);
      return;
    }

    setIsRemovingWatermark(true);
    toast.info(`开始批量去水印（${markedImages.length}张）...`);
    setRemoveProgress({ current: 0, total: markedImages.length });
    setWatermarkDone(false);

    let successCountLocal = 0;
    let failedCountLocal = 0;
    let workingImages = [...images];

    for (let i = 0; i < markedImages.length; i++) {
      const img = markedImages[i];
      workingImages = workingImages.map((item) =>
        item.id === img.id ? { ...item, watermarkStatus: "removing" } : item
      );
      setImages(workingImages);

      let success = false;

      if (dataSource === "api") {
        try {
          success = img.maskData ? await preprocessApi.watermarkManual(img.id, img.maskData) : false;
        } catch {
          success = false;
        }
      } else {
        success = await simulateSingle();
      }

      workingImages = workingImages.map((item) =>
        item.id === img.id
          ? {
              ...item,
              watermarkStatus: success ? "done" : "failed",
              hasMask: success ? false : item.hasMask,
              maskData: success ? undefined : item.maskData,
            }
          : item
      );
      setImages(workingImages);

      if (success) {
        successCountLocal += 1;
      } else {
        failedCountLocal += 1;
      }

      setRemoveProgress({ current: i + 1, total: markedImages.length });
    }

    if (dataSource === "api" && batchId) {
      const detail = await uploadApi.getBatch(batchId);
      if (detail?.images) {
        workingImages = workingImages.map((img) => {
          const remote = detail.images.find((r) => r.id === img.id);
          if (!remote) return img;
          return {
            ...img,
            processedSrc: remote.processed_path ? withCacheBust(toFileUrl(remote.processed_path)) : img.processedSrc,
            watermarkStatus: remote.watermark_removed ? "done" : img.watermarkStatus,
            hasMask: remote.watermark_removed ? false : img.hasMask,
            maskData: remote.watermark_removed ? undefined : img.maskData,
          };
        });
        setImages(workingImages);
      }
    }

    setIsRemovingWatermark(false);
    setWatermarkDone(!hasUnresolvedWatermark(workingImages));

    if (failedCountLocal === 0) {
      toast.success("去水印处理完成！", { description: "确认后点击「进入提示词」继续" });
    } else if (successCountLocal > 0) {
      toast.warning("部分去水印失败", {
        description: `成功 ${successCountLocal} 张，失败 ${failedCountLocal} 张，可修改标记后重试`,
      });
    } else {
      toast.error("去水印失败", { description: "请检查标记区域或服务配置后重试" });
    }
  }, [markedImages, dataSource, batchId, simulateSingle, previewOpen, syncCurrentMask, withCacheBust, images]);

  const handleRemoveWatermarkSingle = useCallback(
    async (img: PreprocessImage | undefined) => {
      const current = syncCurrentMask() ?? img;
      if (!current) return;
      if (!current.hasMask || !current.maskData) {
        toast.info("请先标记水印区域再进行去水印");
        return;
      }

      setWatermarkDone(false);
      setImages((prev) =>
        prev.map((item) =>
          item.id === current.id ? { ...item, watermarkStatus: "removing" } : item
        )
      );

      let success = false;
      toast.info("开始去水印处理...");
      if (dataSource === "api") {
        try {
          success = await preprocessApi.watermarkManual(current.id, current.maskData);
        } catch {
          success = false;
        }
      } else {
        success = await simulateSingle();
      }

      let nextImages = images.map((item) =>
        item.id === current.id
          ? {
              ...item,
              watermarkStatus: success ? "done" : "failed",
              hasMask: success ? false : item.hasMask,
              maskData: success ? undefined : item.maskData,
            }
          : item
      );
      setImages(nextImages);

      if (success && dataSource === "api" && batchId) {
        const detail = await uploadApi.getBatch(batchId);
        if (detail?.images) {
          nextImages = nextImages.map((item) => {
            const remote = detail.images.find((r) => r.id === item.id);
            if (!remote) return item;
            return {
              ...item,
              processedSrc: remote.processed_path ? withCacheBust(toFileUrl(remote.processed_path)) : item.processedSrc,
              watermarkStatus: remote.watermark_removed ? "done" : item.watermarkStatus,
              hasMask: remote.watermark_removed ? false : item.hasMask,
              maskData: remote.watermark_removed ? undefined : item.maskData,
            };
          });
          setImages(nextImages);
        }
      }

      setWatermarkDone(!hasUnresolvedWatermark(nextImages));
      toast[success ? "success" : "error"](success ? "去水印成功" : "去水印失败，请修改标记后重试");
    },
    [dataSource, batchId, simulateSingle, syncCurrentMask, withCacheBust, images]
  );

  const handleSkipWatermark = useCallback(() => {
    toast.info("已跳过去水印，可点击「进入提示词」继续");
    setWatermarkDone(true);
  }, []);

  // ========== 统计数据 ==========
  const successCount = images.filter((i) => i.status === "success").length;
  const failedCount = images.filter((i) => i.status === "failed").length;
  const processingCount = images.filter((i) => i.status === "processing").length;
  const pendingCount = images.filter((i) => i.status === "pending").length;
  const markedCount = images.filter((i) => i.watermarkStatus === "marked").length;
  const resizePercent =
    processProgress.total > 0
      ? Math.round((processProgress.current / processProgress.total) * 100)
      : 0;
  const removePercent =
    removeProgress.total > 0
      ? Math.round((removeProgress.current / removeProgress.total) * 100)
      : 0;

  // ========== 渲染顶部操作按钮 ==========
  const renderActions = () => {
    if (stage === "resize") {
      return (
        <div className="flex items-center gap-3">
          <span className="text-sm text-muted-foreground">
            共 {images.length} 张图片
          </span>
          {failedCount > 0 && !isProcessing && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                images
                  .filter((img) => img.status === "failed")
                  .forEach((img) => handleRetryResize(img.id));
              }}
            >
              <RotateCcw className="w-4 h-4 mr-2" />
              重试失败项 ({failedCount})
            </Button>
          )}
          {resizeDone && successCount > 0 && !isProcessing && (
            <Button
              size="sm"
              onClick={() => {
                setResizeDone(false);
                setStage("watermark");
              }}
            >
              进入去水印
              <ArrowRight className="w-4 h-4 ml-1" />
            </Button>
          )}
          {!resizeDone && (
            <Button
              size="sm"
              onClick={handleStartResize}
              disabled={isProcessing || pendingCount === 0}
            >
              {isProcessing ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  处理中...
                </>
              ) : (
                <>
                  <Play className="w-4 h-4 mr-2" />
                  开始处理
                </>
              )}
            </Button>
          )}
        </div>
      );
    }

    if (stage === "watermark") {
      return (
        <div className="flex items-center gap-3">
          <span className="text-sm text-muted-foreground">
            已标记 {markedCount}/{successCount} 张
          </span>
          {!watermarkDone && (
            <Button variant="outline" size="sm" onClick={handleSkipWatermark}>
              跳过去水印
            </Button>
          )}
          {!watermarkDone && (
            <Button
              size="sm"
              onClick={handleRemoveWatermarks}
              disabled={isRemovingWatermark}
            >
              {isRemovingWatermark ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  批量去水印中...
                </>
              ) : (
                <>
                  <Droplets className="w-4 h-4 mr-2" />
                  批量去水印 {markedCount > 0 ? `(${markedCount}张)` : ""}
                </>
              )}
            </Button>
          )}
          {watermarkDone && (
            <Button
              size="sm"
              onClick={() => setLocation("/prompt")}
            >
              进入提示词
              <ArrowRight className="w-4 h-4 ml-1" />
            </Button>
          )}
        </div>
      );
    }

    return null;
  };

  // ========== 渲染流程步骤指示器 ==========
  const renderStepIndicator = () => {
    const steps = [
      { key: "resize", label: "尺寸标准化", icon: Crop },
      { key: "watermark", label: "去水印标记", icon: Droplets },
      { key: "complete", label: "完成", icon: CheckCircle },
    ];

    return (
      <Card>
        <CardContent className="py-4">
          <div className="flex items-center justify-center gap-4">
            {steps.map((step, index) => {
              const Icon = step.icon;
              const isActive = stage === step.key;
              // resize 步骤在 resizeDone 时也算已完成（绿色对勾）
              // watermark 步骤在 watermarkDone 时也算已完成
              const isPast =
                steps.findIndex((s) => s.key === stage) >
                  steps.findIndex((s) => s.key === step.key) ||
                (step.key === "resize" && stage === "resize" && resizeDone) ||
                (step.key === "watermark" && stage === "watermark" && watermarkDone);

              return (
                <div key={step.key} className="flex items-center gap-4">
                  <div className="flex items-center gap-2">
                    <div
                      className={`w-8 h-8 rounded-full flex items-center justify-center transition-colors ${
                        isPast
                          ? "bg-green-500 text-white"
                          : isActive
                          ? "bg-primary text-white"
                          : "bg-muted text-muted-foreground"
                      }`}
                    >
                      {isPast ? (
                        <CheckCircle className="w-4 h-4" />
                      ) : (
                        <Icon className="w-4 h-4" />
                      )}
                    </div>
                    <span
                      className={`text-sm font-medium ${
                        isPast
                          ? "text-green-600"
                          : isActive
                          ? "text-primary"
                          : "text-muted-foreground"
                      }`}
                    >
                      {step.label}
                    </span>
                  </div>
                  {index < steps.length - 1 && (
                    <div
                      className={`w-16 h-px ${isPast ? "bg-green-500" : "bg-border"}`}
                    />
                  )}
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>
    );
  };

  // ========== 渲染进度条 ==========
  const renderProgressBar = () => {
    if (stage === "resize" && isProcessing) {
      return (
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium">尺寸标准化处理进度</span>
              <span className="text-sm text-muted-foreground">
                {resizePercent}% ({processProgress.current}/{processProgress.total})
              </span>
            </div>
            <Progress value={resizePercent} className="h-2" />
            <div className="flex items-center gap-6 mt-3 text-sm">
              <div className="flex items-center gap-1.5">
                <CheckCircle className="w-3.5 h-3.5 text-green-500" />
                <span>成功 {successCount}</span>
              </div>
              <div className="flex items-center gap-1.5">
                <Loader2 className="w-3.5 h-3.5 text-primary animate-spin" />
                <span>处理中 {processingCount}</span>
              </div>
              <div className="flex items-center gap-1.5">
                <AlertCircle className="w-3.5 h-3.5 text-destructive" />
                <span>失败 {failedCount}</span>
              </div>
            </div>
          </CardContent>
        </Card>
      );
    }

    if (stage === "watermark" && isRemovingWatermark) {
      return (
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium">去水印处理进度</span>
              <span className="text-sm text-muted-foreground">
                {removePercent}% ({removeProgress.current}/{removeProgress.total})
              </span>
            </div>
            <Progress value={removePercent} className="h-2" />
          </CardContent>
        </Card>
      );
    }

    return null;
  };

  // ========== 渲染图片卡片 ==========
  const renderImageCard = (img: PreprocessImage, index: number) => {
    const isResizeStage = stage === "resize";
    const isWatermarkStage = stage === "watermark";
    const showProcessedImage = img.status === "success" && img.processedSrc;

    return (
      <Card
        key={img.id}
        className="overflow-hidden transition-all hover:shadow-md group"
      >
        {/* 9:16 图片预览区域 */}
        <div className="relative aspect-[9/16] bg-muted overflow-hidden">
          {/* 阶段一待处理：显示裁剪/扩图预览 */}
          {isResizeStage && img.status === "pending" && img.processMode === "crop" && (
            <DraggableCropPreview img={img} onOffsetChange={handleCropOffsetChange} />
          )}
          {isResizeStage && img.status === "pending" && img.processMode === "ai-expand" && (
            <DraggableExpandPreview img={img} onOffsetChange={handleExpandOffsetChange} />
          )}

          {/* 处理中/完成/失败 或 阶段二 */}
          {(img.status !== "pending" || !isResizeStage) && (
            <img
              src={showProcessedImage ? img.processedSrc : img.originalSrc}
              alt={img.name}
              className="w-full h-full object-cover"
            />
          )}

          {/* 处理中遮罩 */}
          {img.status === "processing" && (
            <div className="absolute inset-0 bg-black/60 flex flex-col items-center justify-center">
              <Loader2 className="w-10 h-10 text-white animate-spin mb-3" />
              <span className="text-white text-sm font-medium">
                {img.processMode === "crop" ? "9:16裁剪中..." : "AI扩图中..."}
              </span>
              {/* indeterminate 进度条 */}
              <div className="w-3/4 mt-3 h-1 bg-white/20 rounded-full overflow-hidden">
                <div className="h-full bg-white/80 rounded-full animate-indeterminate" />
              </div>
            </div>
          )}

          {/* 失败遮罩 */}
          {img.status === "failed" && (
            <div className="absolute inset-0 bg-black/60 flex flex-col items-center justify-center">
              <AlertCircle className="w-10 h-10 text-red-400 mb-3" />
              <span className="text-white text-sm mb-2">处理失败</span>
              <Button
                size="sm"
                variant="outline"
                className="text-white border-white/50 hover:bg-white/20"
                onClick={(e) => {
                  e.stopPropagation();
                  handleRetryResize(img.id);
                }}
              >
                <RotateCcw className="w-3 h-3 mr-1" />
                重试
              </Button>
            </div>
          )}

          {/* 去水印中遮罩 */}
          {img.watermarkStatus === "removing" && (
            <div className="absolute inset-0 bg-black/60 flex flex-col items-center justify-center">
              <Loader2 className="w-10 h-10 text-white animate-spin mb-3" />
              <span className="text-white text-sm font-medium">去水印中...</span>
            </div>
          )}
          {img.watermarkStatus === "removing" && (
            <div className="absolute left-0 right-0 bottom-0 h-1 bg-white/30">
              <div className="h-full bg-primary animate-indeterminate" />
            </div>
          )}

          {/* 成功标记 */}
          {img.status === "success" && stage === "resize" && (
            <div className="absolute top-2 right-2">
              <CheckCircle className="w-6 h-6 text-green-500 drop-shadow-lg" />
            </div>
          )}

          {/* 水印标记徽章 */}
          {isWatermarkStage && img.watermarkStatus === "marked" && (
            <div className="absolute top-2 left-2">
              <Badge className="bg-red-500/90 text-white text-xs">已标记水印</Badge>
            </div>
          )}

          {/* 去水印完成标记 */}
          {isWatermarkStage && img.watermarkStatus === "done" && (
            <div className="absolute top-2 left-2">
              <Badge className="bg-green-500/90 text-white text-xs">水印已去除</Badge>
            </div>
          )}
          {isWatermarkStage && img.watermarkStatus === "failed" && (
            <div className="absolute top-2 left-2">
              <Badge className="bg-red-500/90 text-white text-xs">去水印失败</Badge>
            </div>
          )}

          {/* 悬浮预览按钮（去水印阶段：打开标记工具，可重标） */}
          {isWatermarkStage && img.status === "success" && !isRemovingWatermark && (
            <div
              className="absolute inset-0 bg-black/0 group-hover:bg-black/40 transition-colors flex items-center justify-center cursor-pointer"
              onClick={() => handleOpenPreview(index)}
            >
              <div className="opacity-0 group-hover:opacity-100 transition-opacity flex flex-col items-center gap-2">
                <Eye className="w-8 h-8 text-white" />
                <span className="text-white text-sm font-medium">
                  {img.watermarkStatus === "done" ? "查看并重标水印" : "点击标记水印"}
                </span>
              </div>
            </div>
          )}

          {/* 悬浮查看大图（resize 阶段成功后） */}
          {isResizeStage && img.status === "success" && (
            <div
              className="absolute inset-0 bg-black/0 group-hover:bg-black/40 transition-colors flex items-center justify-center cursor-pointer"
              onClick={() => {
                setSimplePreviewIndex(index);
                setSimplePreviewOpen(true);
              }}
            >
              <div className="opacity-0 group-hover:opacity-100 transition-opacity flex flex-col items-center gap-2">
                <ZoomIn className="w-8 h-8 text-white" />
                <span className="text-white text-sm font-medium">查看大图</span>
              </div>
            </div>
          )}
        </div>

        {/* 底部信息区 */}
        <CardContent className="p-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm truncate flex-1 mr-2 font-medium">{img.name}</span>
            {img.status === "success" && stage === "resize" && (
              <Badge variant="default" className="bg-green-500 text-xs">完成</Badge>
            )}
            {img.status === "failed" && (
              <Badge variant="destructive" className="text-xs">失败</Badge>
            )}
            {img.status === "pending" && (
              <Badge variant="secondary" className="text-xs">等待</Badge>
            )}
            {img.status === "processing" && (
              <Badge className="bg-primary text-xs">处理中</Badge>
            )}
          </div>

          {/* 原始尺寸信息 */}
          {isResizeStage && img.status === "pending" && (
            <div className="text-xs text-muted-foreground">
              原始: {img.naturalWidth}×{img.naturalHeight}
              {Math.abs(img.aspectRatio - TARGET_RATIO) < 0.01
                ? " (已符合9:16)"
                : img.aspectRatio > TARGET_RATIO
                ? " (偏宽→左右裁剪/上下扩展)"
                : " (偏高→上下裁剪/左右扩展)"}
            </div>
          )}

          {/* 处理模式选择 */}
          {isResizeStage && (img.status === "pending" || img.status === "failed") && !isProcessing && (
            <div className="flex gap-1">
              <Button
                size="sm"
                variant={img.processMode === "crop" ? "default" : "outline"}
                className="flex-1 h-7 text-xs"
                onClick={() => handleChangeMode(img.id, "crop")}
              >
                <Crop className="w-3 h-3 mr-1" />
                9:16裁剪
              </Button>
              <Button
                size="sm"
                variant={img.processMode === "ai-expand" ? "default" : "outline"}
                className="flex-1 h-7 text-xs"
                onClick={() => handleChangeMode(img.id, "ai-expand")}
              >
                <Sparkles className="w-3 h-3 mr-1" />
                AI扩图
              </Button>
            </div>
          )}

          {/* 偏移量重置按钮 */}
          {isResizeStage && img.status === "pending" && !isProcessing && (
            <>
              {img.processMode === "crop" && img.cropOffset !== 0 && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="w-full h-6 text-xs text-muted-foreground"
                  onClick={() => handleCropOffsetChange(img.id, 0)}
                >
                  <RotateCcw className="w-3 h-3 mr-1" />
                  重置居中
                </Button>
              )}
              {img.processMode === "ai-expand" && img.expandOffset !== 0 && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="w-full h-6 text-xs text-muted-foreground"
                  onClick={() => handleExpandOffsetChange(img.id, 0)}
                >
                  <RotateCcw className="w-3 h-3 mr-1" />
                  重置居中
                </Button>
              )}
            </>
          )}

          {/* 已选择的处理模式标签 */}
          {isResizeStage && (img.status === "processing" || img.status === "success") && (
            <div className="text-xs text-muted-foreground">
              {img.processMode === "crop" ? "📐 9:16裁剪" : "✨ AI扩图"}
            </div>
          )}

          {/* 去水印阶段的操作提示 */}
          {isWatermarkStage && img.status === "success" && !isRemovingWatermark && (
            <Button
              size="sm"
              variant="outline"
              className="w-full h-7 text-xs"
              onClick={() => handleOpenPreview(index)}
            >
              <Eye className="w-3 h-3 mr-1" />
              {img.watermarkStatus === "done"
                ? "查看并重标水印"
                : img.watermarkStatus === "marked"
                ? "修改水印标记"
                : "标记水印位置"}
            </Button>
          )}
        </CardContent>
      </Card>
    );
  };

  // ========== 大图预览弹窗 ==========
  const currentPreviewImage = successImages[previewIndex];

  const renderPreviewDialog = () => (
    <Dialog open={previewOpen} onOpenChange={(open) => !open && handleClosePreview()}>
      <DialogContent
        className="w-[min(1100px,96vw)] max-h-[95vh] p-0 overflow-hidden"
        showCloseButton={false}
      >
        <div className="flex flex-col h-full">
          <DialogHeader className="px-4 py-3 border-b gap-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <DialogTitle className="text-base truncate">
                  {currentPreviewImage?.name}
                </DialogTitle>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <Badge variant="outline" className="text-xs">
                    {previewIndex + 1} / {successImages.length}
                  </Badge>
                  {currentPreviewImage?.hasMask && (
                    <Badge className="bg-red-500/90 text-white text-xs">已标记</Badge>
                  )}
                </div>
              </div>
              <Button size="sm" variant="ghost" onClick={handleClosePreview}>
                <X className="w-4 h-4" />
              </Button>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                onClick={() => handleRemoveWatermarkSingle(currentPreviewImage)}
                disabled={!currentPreviewImage?.hasMask || currentPreviewImage?.watermarkStatus === "removing"}
              >
                <Droplets className="w-4 h-4 mr-1" />
                去水印
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={handlePrevImage}
                disabled={successImages.length <= 1}
              >
                <ChevronLeft className="w-4 h-4" />
                上一张
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={handleNextImage}
                disabled={successImages.length <= 1}
              >
                下一张
                <ChevronRight className="w-4 h-4" />
              </Button>
            </div>
          </DialogHeader>

          <div className="flex-1 p-4 overflow-auto">
            {currentPreviewImage && (
              <WatermarkCanvas
                key={currentPreviewImage.id}
                ref={canvasRef}
                imageSrc={currentPreviewImage.processedSrc || currentPreviewImage.originalSrc}
                width={900}
                height={1600}
                initialMaskData={currentPreviewImage.maskData}
                onMaskChange={handleMaskChange}
              />
            )}
            {currentPreviewImage?.watermarkStatus === "removing" && (
              <div className="mt-3 flex items-center gap-2 text-sm text-primary">
                <Loader2 className="w-4 h-4 animate-spin" />
                去水印处理中，请稍候...
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );

  // ========== 主渲染 ==========
  return (
    <MainLayout title="预处理" actions={renderActions()}>
      <div className="flex flex-col gap-5">
        {/* 流程步骤指示器 */}
        {renderStepIndicator()}

        {/* 进度条 */}
        {renderProgressBar()}

        {/* 去水印阶段提示 */}
        {stage === "watermark" && !isRemovingWatermark && (
          <Card className="border-primary/20 bg-primary/5">
            <CardContent className="py-3 px-4">
              <div className="flex items-center gap-3">
                <Droplets className="w-5 h-5 text-primary shrink-0" />
                <div>
                  <p className="text-sm font-medium text-primary">
                    请点击图片标记水印位置
                  </p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    点击图片进入大图预览，使用红色画笔涂抹水印区域。标记完成后点击"去水印"按钮一次性处理所有标记的图片。
                    已显示“水印已去除”的图片也可再次进入大图重新标记并重试去水印。
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
        )}

        {/* 图片网格 */}
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4">
          {images.map((img, index) => renderImageCard(img, index))}
        </div>

        {images.length === 0 && imagesLoaded && (
          <div className="text-center py-16">
            <p className="text-muted-foreground">暂无待处理的图片</p>
            <p className="text-sm text-muted-foreground mt-2">
              请先在"素材上传"页面上传底图
            </p>
          </div>
        )}
      </div>

      {/* 大图预览弹窗 */}
      {renderPreviewDialog()}

      {/* 纯查看大图弹窗（resize 阶段） */}
      <Dialog open={simplePreviewOpen} onOpenChange={setSimplePreviewOpen}>
        <DialogContent className="max-w-4xl max-h-[90vh] p-0 overflow-hidden">
          <DialogHeader className="p-4 pb-2">
            <DialogTitle className="text-base">
              {images[simplePreviewIndex]?.name ?? "预览"}
            </DialogTitle>
          </DialogHeader>
          <div className="flex items-center justify-center p-4 pt-0 max-h-[80vh] overflow-auto">
            {images[simplePreviewIndex] && (
              <img
                src={
                  images[simplePreviewIndex].processedSrc ||
                  images[simplePreviewIndex].originalSrc
                }
                alt={images[simplePreviewIndex].name}
                className="max-w-full max-h-[75vh] object-contain rounded"
              />
            )}
          </div>
        </DialogContent>
      </Dialog>
    </MainLayout>
  );
}
