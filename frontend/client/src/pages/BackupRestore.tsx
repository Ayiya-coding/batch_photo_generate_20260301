import { useRef, useState, type ChangeEvent } from "react";
import { MainLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import {
  backupApi,
  type BackupImportResult,
  type BackupPayload,
} from "@/lib/api";
import {
  AlertTriangle,
  Database,
  Download,
  FileText,
  Loader2,
  MessageSquare,
  Settings2,
  Upload,
} from "lucide-react";

function formatDisplayTime(value?: string | null) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function buildBackupFilename(exportedAt?: string) {
  const date = exportedAt ? new Date(exportedAt) : new Date();
  const safeDate = Number.isNaN(date.getTime()) ? new Date() : date;
  const pad = (value: number) => String(value).padStart(2, "0");
  return `ai-image-backup_${safeDate.getFullYear()}${pad(safeDate.getMonth() + 1)}${pad(safeDate.getDate())}_${pad(safeDate.getHours())}${pad(safeDate.getMinutes())}${pad(safeDate.getSeconds())}.json`;
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

export default function BackupRestore() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isExporting, setIsExporting] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [restoreSettings, setRestoreSettings] = useState(true);
  const [restorePrompts, setRestorePrompts] = useState(true);
  const [selectedFileName, setSelectedFileName] = useState("");
  const [backupPayload, setBackupPayload] = useState<BackupPayload | null>(null);
  const [lastExport, setLastExport] = useState<BackupPayload | null>(null);
  const [lastImport, setLastImport] = useState<BackupImportResult | null>(null);

  const summary = backupPayload?.summary ?? {
    settings_count: backupPayload?.settings.length ?? 0,
    prompt_count: backupPayload?.prompt_templates.length ?? 0,
  };

  const handleExport = async () => {
    setIsExporting(true);
    const toastId = toast.loading("正在生成备份文件...");
    try {
      const payload = await backupApi.exportData();
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
      link.download = buildBackupFilename(payload.exported_at);
      link.click();
      URL.revokeObjectURL(downloadUrl);

      setLastExport(payload);
      toast.dismiss(toastId);
      toast.success("备份已导出到本地", {
        description: `包含 ${payload.summary?.settings_count ?? payload.settings.length} 项设置、${payload.summary?.prompt_count ?? payload.prompt_templates.length} 条提示词`,
      });
    } catch {
      toast.dismiss(toastId);
      toast.error("备份导出失败，请重试");
    } finally {
      setIsExporting(false);
    }
  };

  const handlePickFile = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    try {
      const raw = await file.text();
      const parsed = JSON.parse(raw);
      if (!isBackupPayload(parsed)) {
        setSelectedFileName("");
        setBackupPayload(null);
        toast.error("备份文件格式不正确");
        return;
      }
      setSelectedFileName(file.name);
      setBackupPayload(parsed);
      setLastImport(null);
      toast.success("备份文件已载入", {
        description: `检测到 ${parsed.settings.length} 项设置、${parsed.prompt_templates.length} 条提示词`,
      });
    } catch {
      toast.error("无法读取备份文件，请确认是有效的 JSON");
    } finally {
      event.target.value = "";
    }
  };

  const handleImport = async () => {
    if (!backupPayload) {
      toast.info("请先选择备份文件");
      return;
    }
    if (!restoreSettings && !restorePrompts) {
      toast.warning("请至少选择一个恢复项");
      return;
    }

    const confirmed = window.confirm(
      "导入备份会覆盖当前选中的数据项，确定继续吗？",
    );
    if (!confirmed) return;

    setIsImporting(true);
    const toastId = toast.loading("正在导入备份...");
    try {
      const result = await backupApi.importData(
        backupPayload,
        restoreSettings,
        restorePrompts,
      );
      if (!result) {
        toast.dismiss(toastId);
        return;
      }

      setLastImport(result);
      toast.dismiss(toastId);
      toast.success("备份导入完成", {
        description: `设置新增 ${result.settings_created} 项、更新 ${result.settings_updated} 项；提示词恢复 ${result.prompts_created} 条`,
      });
    } catch {
      toast.dismiss(toastId);
      toast.error("备份导入失败，请检查文件内容");
    } finally {
      setIsImporting(false);
    }
  };

  return (
    <MainLayout title="备份恢复">
      <div className="max-w-5xl mx-auto space-y-6 pb-8">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Database className="w-5 h-5" />
              手动导出备份
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="rounded-xl border border-border bg-muted/20 p-4">
              <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <Badge variant="secondary">JSON 备份</Badge>
                    <Badge variant="outline">含敏感配置</Badge>
                  </div>
                  <p className="text-sm text-foreground">
                    一次性导出系统设置与提示词词库，适合在本地手动留档或迁移到其他环境。
                  </p>
                  <p className="text-xs text-muted-foreground">
                    当前版本不包含图片文件、批次过程数据和模板图库，只备份配置与词库。
                  </p>
                </div>
                <Button onClick={handleExport} disabled={isExporting}>
                  {isExporting ? (
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  ) : (
                    <Download className="w-4 h-4 mr-2" />
                  )}
                  {isExporting ? "导出中..." : "立即导出备份"}
                </Button>
              </div>
            </div>

            <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
              <div className="flex items-start gap-3">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <div>
                  备份文件会包含 API Key 等敏感配置，请只保存在可信目录，不建议发给第三方。
                </div>
              </div>
            </div>

            {lastExport && (
              <div className="grid gap-3 md:grid-cols-3">
                <div className="rounded-lg border p-4">
                  <div className="text-xs text-muted-foreground mb-1">最近导出时间</div>
                  <div className="text-sm font-medium">
                    {formatDisplayTime(lastExport.exported_at)}
                  </div>
                </div>
                <div className="rounded-lg border p-4">
                  <div className="text-xs text-muted-foreground mb-1">设置项</div>
                  <div className="text-sm font-medium">
                    {lastExport.summary?.settings_count ?? lastExport.settings.length} 项
                  </div>
                </div>
                <div className="rounded-lg border p-4">
                  <div className="text-xs text-muted-foreground mb-1">提示词条数</div>
                  <div className="text-sm font-medium">
                    {lastExport.summary?.prompt_count ?? lastExport.prompt_templates.length} 条
                  </div>
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Upload className="w-5 h-5" />
              导入备份
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-col gap-3 rounded-xl border border-border bg-muted/20 p-4 lg:flex-row lg:items-center lg:justify-between">
              <div className="space-y-1">
                <p className="text-sm text-foreground">
                  选择之前导出的 JSON 备份文件，然后按需恢复系统设置和提示词词库。
                </p>
                <p className="text-xs text-muted-foreground">
                  导入提示词词库时会先清空当前词库，再按备份内容恢复。
                </p>
              </div>
              <div className="flex gap-2">
                <Button variant="outline" onClick={handlePickFile}>
                  <FileText className="w-4 h-4 mr-2" />
                  选择备份文件
                </Button>
                <Button onClick={handleImport} disabled={isImporting || !backupPayload}>
                  {isImporting ? (
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  ) : (
                    <Upload className="w-4 h-4 mr-2" />
                  )}
                  {isImporting ? "导入中..." : "开始导入"}
                </Button>
              </div>
            </div>

            <input
              ref={fileInputRef}
              type="file"
              accept=".json,application/json"
              className="hidden"
              onChange={handleFileChange}
            />

            {backupPayload ? (
              <>
                <div className="grid gap-3 md:grid-cols-5">
                  <div className="rounded-lg border p-4 md:col-span-2">
                    <div className="text-xs text-muted-foreground mb-1">备份文件</div>
                    <div className="text-sm font-medium break-all">
                      {selectedFileName || "未命名文件"}
                    </div>
                  </div>
                  <div className="rounded-lg border p-4">
                    <div className="text-xs text-muted-foreground mb-1">版本</div>
                    <div className="text-sm font-medium">{backupPayload.version}</div>
                  </div>
                  <div className="rounded-lg border p-4">
                    <div className="text-xs text-muted-foreground mb-1">设置项</div>
                    <div className="text-sm font-medium">{summary.settings_count} 项</div>
                  </div>
                  <div className="rounded-lg border p-4">
                    <div className="text-xs text-muted-foreground mb-1">提示词</div>
                    <div className="text-sm font-medium">{summary.prompt_count} 条</div>
                  </div>
                </div>

                <div className="rounded-xl border p-4 space-y-4">
                  <div className="text-sm text-muted-foreground">
                    导出时间：{formatDisplayTime(backupPayload.exported_at)}
                  </div>
                  <div className="grid gap-4 md:grid-cols-2">
                    <div className="flex items-center justify-between rounded-lg border p-4">
                      <div className="flex items-start gap-3">
                        <Settings2 className="mt-0.5 h-4 w-4 text-muted-foreground" />
                        <div>
                          <Label className="text-sm">恢复系统设置</Label>
                          <p className="text-xs text-muted-foreground mt-1">
                            覆盖 API Key、生成参数、导出目录等配置。
                          </p>
                        </div>
                      </div>
                      <Switch
                        checked={restoreSettings}
                        onCheckedChange={setRestoreSettings}
                      />
                    </div>

                    <div className="flex items-center justify-between rounded-lg border p-4">
                      <div className="flex items-start gap-3">
                        <MessageSquare className="mt-0.5 h-4 w-4 text-muted-foreground" />
                        <div>
                          <Label className="text-sm">恢复提示词词库</Label>
                          <p className="text-xs text-muted-foreground mt-1">
                            先清空当前词库，再恢复备份里的全部提示词。
                          </p>
                        </div>
                      </div>
                      <Switch
                        checked={restorePrompts}
                        onCheckedChange={setRestorePrompts}
                      />
                    </div>
                  </div>
                </div>
              </>
            ) : (
              <div className="rounded-xl border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
                请选择一个备份文件，系统会在这里显示备份摘要和可恢复项。
              </div>
            )}

            {lastImport && (
              <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
                <div className="text-sm font-medium text-emerald-900">最近一次导入结果</div>
                <div className="mt-2 grid gap-3 md:grid-cols-4 text-sm text-emerald-900">
                  <div>设置新增 {lastImport.settings_created} 项</div>
                  <div>设置更新 {lastImport.settings_updated} 项</div>
                  <div>旧提示词清理 {lastImport.prompts_deleted} 条</div>
                  <div>新提示词恢复 {lastImport.prompts_created} 条</div>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </MainLayout>
  );
}
