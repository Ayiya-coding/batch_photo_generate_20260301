import { useState, useEffect, useRef, useCallback } from "react";
import { MainLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";
import {
  Play,
  Pause,
  CheckCircle,
  AlertCircle,
  Loader2,
  RotateCcw,
  Info,
  ImageIcon,
  Zap,
  BarChart3,
} from "lucide-react";
import { useUpload } from "@/contexts/UploadContext";
import {
  generateApi,
  type GenerateOverview,
  type GenerateProgressInfo,
} from "@/lib/api";

/** 轮询间隔 ms */
const POLL_INTERVAL = 2000;

export default function BatchGenerate() {
  const { batchId } = useUpload();

  // --- 状态 ---
  const [overview, setOverview] = useState<GenerateOverview | null>(null);
  const [progress, setProgress] = useState<GenerateProgressInfo | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [loading, setLoading] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const notifiedRef = useRef(false); // 防止重复通知

  // --- 加载概览 ---
  const fetchOverview = useCallback(async () => {
    if (!batchId) return;
    const data = await generateApi.overview(batchId);
    if (data) setOverview(data);
  }, [batchId]);

  // --- 轮询进度 ---
  const fetchProgress = useCallback(async () => {
    if (!batchId) return;
    const data = await generateApi.progress(batchId);
    if (!data) return;
    setProgress(data);

    // 同步刷新概览数字
    fetchOverview();

    if (data.status === "completed" || data.status === "error" || data.status === "cancelled") {
      stopPolling();
      setIsRunning(false);

      if (data.status === "completed" && !notifiedRef.current) {
        notifiedRef.current = true;
        notifyComplete(data.completed, data.failed);
      }
      if (data.status === "error") {
        toast.error("生图任务出错，请查看日志");
      }
      if (data.status === "cancelled") {
        toast.info("批量生图已中断，剩余任务已保留为待处理");
      }
    }
  }, [batchId, fetchOverview]);

  const startPolling = useCallback(() => {
    stopPolling();
    pollRef.current = setInterval(fetchProgress, POLL_INTERVAL);
    fetchProgress(); // 立即拉一次
  }, [fetchProgress]);

  function stopPolling() {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  // --- 完成通知（声音 + 浏览器 Notification） ---
  function notifyComplete(completed: number, failed: number) {
    toast.success(`批量生图完成！成功 ${completed} 张，失败 ${failed} 张`);

    // 声音提示
    try {
      const ctx = new AudioContext();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.frequency.value = 880;
      gain.gain.value = 0.3;
      osc.start();
      osc.stop(ctx.currentTime + 0.3);
    } catch {
      /* 静默 */
    }

    // 浏览器通知
    if ("Notification" in window && Notification.permission === "granted") {
      new Notification("批量生图完成", {
        body: `成功 ${completed} 张，失败 ${failed} 张`,
      });
    } else if ("Notification" in window && Notification.permission !== "denied") {
      Notification.requestPermission();
    }
  }

  // --- 初始化 ---
  useEffect(() => {
    if (!batchId) return;
    fetchOverview();
    // 检查是否有正在运行的任务
    generateApi.progress(batchId).then((data) => {
      if (data) {
        setProgress(data);
        if (data.status === "running" || data.status === "cancelling") {
          setIsRunning(true);
          notifiedRef.current = false;
          startPolling();
        }
      }
    });
    return () => stopPolling();
  }, [batchId]);

  // --- 开始生图 ---
  const handleStart = async () => {
    if (!batchId) {
      toast.error("请先上传素材并生成提示词");
      return;
    }
    setLoading(true);
    notifiedRef.current = false;
    const result = await generateApi.start(batchId);
    setLoading(false);

    if (result) {
      toast.info("批量生图已启动", {
        description: `${result.pending_count} 个任务，引擎: ${result.engine}`,
      });
      setIsRunning(true);
      startPolling();
    }
  };

  // --- 失败重试 ---
  const handleRetry = async () => {
    if (!batchId) return;
    setLoading(true);
    notifiedRef.current = false;
    const result = await generateApi.retry(batchId);
    setLoading(false);

    if (result) {
      toast.info(`正在重试 ${result.retry_count} 个失败任务`);
      setIsRunning(true);
      startPolling();
    }
  };

  const handleCancel = async () => {
    if (!batchId) return;
    const result = await generateApi.cancel(batchId);
    if (result !== undefined) {
      toast.info("已发送中断请求，当前任务会在安全点停止");
      setIsRunning(true);
      startPolling();
    }
  };

  // --- 计算数据 ---
  const totalProgress = progress?.progress ?? overview?.progress ?? 0;
  const totalTasks = progress?.total ?? overview?.total_tasks ?? 0;
  const completedTasks = progress?.completed ?? overview?.completed ?? 0;
  const failedTasks = progress?.failed ?? overview?.failed ?? 0;
  const pendingTasks = overview?.pending ?? 0;
  const perImage = progress?.per_image ?? {};
  const logs = progress?.logs ?? [];
  const status = progress?.status ?? "not_started";

  // --- 无批次提示 ---
  if (!batchId) {
    return (
      <MainLayout title="批量生图">
        <div className="flex flex-col items-center justify-center h-[60vh] text-muted-foreground gap-4">
          <ImageIcon className="w-16 h-16 opacity-30" />
          <p className="text-lg">请先在「素材上传」页面上传图片，并在「提示词」页面生成提示词</p>
        </div>
      </MainLayout>
    );
  }

  return (
    <MainLayout
      title="批量生图"
      actions={
        <div className="flex items-center gap-3">
          <span className="text-sm text-muted-foreground">
            进度: {completedTasks}/{totalTasks}
          </span>
          {isRunning ? (
            <>
              <Badge className="bg-primary text-white animate-pulse">
                {progress?.status === "cancelling" ? "中断中..." : "生成中..."}
              </Badge>
              <Button
                variant="destructive"
                size="sm"
                onClick={handleCancel}
                disabled={loading || progress?.status === "cancelling"}
              >
                <Pause className="w-4 h-4 mr-2" />
                {progress?.status === "cancelling" ? "中断请求已发送" : "中断任务"}
              </Button>
            </>
          ) : (
            <>
              {failedTasks > 0 && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleRetry}
                  disabled={loading}
                >
                  <RotateCcw className="w-4 h-4 mr-2" />
                  重试失败 ({failedTasks})
                </Button>
              )}
              <Button
                size="sm"
                onClick={handleStart}
                disabled={loading || pendingTasks === 0}
              >
                {loading ? (
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                ) : (
                  <Play className="w-4 h-4 mr-2" />
                )}
                开始生成
              </Button>
            </>
          )}
        </div>
      }
    >
      <div className="space-y-4">
        {/* ===== 任务概览 ===== */}
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="text-base flex items-center gap-2">
              <BarChart3 className="w-4 h-4" />
              任务概览
            </CardTitle>
          </CardHeader>
          <CardContent className="pb-4">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
              <OverviewItem
                label="底图数量"
                value={`${overview?.base_images ?? 0} 张`}
              />
              <OverviewItem
                label="人群类型"
                value={`${overview?.crowd_types ?? 19} 种`}
              />
              <OverviewItem
                label="提示词/类型"
                value={`${overview?.styles_per_type ?? 5} 个`}
              />
              <OverviewItem
                label="总计生成"
                value={`${totalTasks} 张`}
                highlight
              />
            </div>

            {/* 引擎信息 */}
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Zap className="w-4 h-4" />
              <span>AI引擎: 系统设置页统一配置 | 智能并发（初始10线程，动态调整）</span>
            </div>
          </CardContent>
        </Card>

        {/* ===== 总体进度 ===== */}
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="text-base flex items-center gap-2">
              {isRunning ? (
                <Loader2 className="w-4 h-4 animate-spin text-primary" />
              ) : status === "completed" ? (
                <CheckCircle className="w-4 h-4 text-green-500" />
              ) : status === "error" ? (
                <AlertCircle className="w-4 h-4 text-destructive" />
              ) : status === "cancelled" ? (
                <Pause className="w-4 h-4 text-amber-500" />
              ) : (
                <Info className="w-4 h-4" />
              )}
              实时进度
            </CardTitle>
          </CardHeader>
          <CardContent className="pb-4 space-y-4">
            {/* 总进度条 */}
            <div>
              <div className="flex justify-between text-sm mb-1">
                <span className="text-muted-foreground">总进度</span>
                <span className="font-medium">
                  {totalProgress}% ({completedTasks}/{totalTasks})
                </span>
              </div>
              <Progress value={totalProgress} className="h-3" />
              <div className="flex gap-4 mt-2 text-xs text-muted-foreground">
                <span className="flex items-center gap-1">
                  <CheckCircle className="w-3 h-3 text-green-500" />
                  成功: {completedTasks}
                </span>
                <span className="flex items-center gap-1">
                  <AlertCircle className="w-3 h-3 text-destructive" />
                  失败: {failedTasks}
                </span>
                <span>等待: {Math.max(0, totalTasks - completedTasks - failedTasks)}</span>
              </div>
            </div>

            {/* 每张底图独立进度 */}
            {Object.keys(perImage).length > 0 && (
              <div className="space-y-2">
                <p className="text-sm font-medium text-muted-foreground">各底图进度</p>
                <ScrollArea className="max-h-[240px]">
                  <div className="space-y-2 pr-2">
                    {Object.entries(perImage).map(([imgId, info]) => (
                      <div key={imgId} className="flex items-center gap-3">
                        <span className="text-xs w-28 truncate shrink-0" title={info.filename}>
                          {info.filename}
                        </span>
                        <Progress
                          value={info.progress}
                          className="h-2 flex-1"
                        />
                        <span className="text-xs text-muted-foreground w-20 text-right shrink-0">
                          {info.progress}% ({info.completed}/{info.total})
                        </span>
                        {info.failed > 0 && (
                          <Badge variant="destructive" className="text-[10px] px-1 py-0">
                            {info.failed}失败
                          </Badge>
                        )}
                      </div>
                    ))}
                  </div>
                </ScrollArea>
              </div>
            )}
          </CardContent>
        </Card>

        {/* ===== 日志 ===== */}
        {logs.length > 0 && (
          <Card>
            <CardHeader className="py-3">
              <CardTitle className="text-base">运行日志</CardTitle>
            </CardHeader>
            <CardContent className="pb-4">
              <ScrollArea className="h-[160px]">
                <div className="space-y-1 font-mono text-xs">
                  {logs.map((log, i) => (
                    <div
                      key={i}
                      className={
                        log.includes("[FAIL]")
                          ? "text-destructive"
                          : log.includes("[OK]")
                            ? "text-green-600"
                            : "text-muted-foreground"
                      }
                    >
                      {log}
                    </div>
                  ))}
                </div>
              </ScrollArea>
            </CardContent>
          </Card>
        )}

        {/* 已完成提示 */}
        {status === "completed" && completedTasks > 0 && (
          <div className="text-center text-sm text-muted-foreground py-2">
            已完成的图片已实时进入审核队列，可前往「审核分类」页面查看
          </div>
        )}
      </div>
    </MainLayout>
  );
}

/** 概览数字小卡片 */
function OverviewItem({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: boolean;
}) {
  return (
    <div className="text-center p-3 rounded-lg bg-muted/50">
      <p className="text-xs text-muted-foreground mb-1">{label}</p>
      <p className={`text-lg font-semibold ${highlight ? "text-primary" : ""}`}>
        {value}
      </p>
    </div>
  );
}
