/**
 * API 服务层 — 统一 axios 封装 + 全端点类型化调用
 *
 * 所有页面通过此模块与后端通信，不再直接使用 axios。
 * 后端统一响应格式: { code: number; message: string; data?: any }
 */
import axios, { type AxiosRequestConfig } from "axios";
import { toast } from "sonner";
import { API } from "./apiConfig";

// ---------- axios 实例 ----------

const http = axios.create({
  timeout: 60_000, // 生图等长任务可能较慢
  headers: { "Content-Type": "application/json" },
});

// 响应拦截：统一处理错误
http.interceptors.response.use(
  (res) => res,
  (err) => {
    const msg =
      err.response?.data?.message ||
      err.response?.data?.detail ||
      err.message ||
      "网络请求失败";
    toast.error(msg);
    return Promise.reject(err);
  },
);

// ---------- 重试工具 ----------

async function withRetry<T>(
  fn: () => Promise<T>,
  maxRetries = 3,
  baseDelay = 1000,
): Promise<T> {
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      return await fn();
    } catch (err: unknown) {
      const isLast = attempt === maxRetries - 1;
      // 4xx 客户端错误不重试
      if (
        err &&
        typeof err === "object" &&
        "response" in err &&
        (err as { response?: { status?: number } }).response?.status &&
        (err as { response: { status: number } }).response.status >= 400 &&
        (err as { response: { status: number } }).response.status < 500
      ) {
        throw err;
      }
      if (isLast) throw err;
      const delay = baseDelay * Math.pow(2, attempt);
      await new Promise((r) => setTimeout(r, delay));
    }
  }
  throw new Error("重试次数已用尽");
}

// ---------- 通用响应类型 ----------

export interface BaseResponse<T = Record<string, unknown>> {
  code: number;
  message: string;
  data?: T;
}

/** 提取 data 字段的快捷方法（自动重试网络/服务端错误） */
async function unwrap<T>(promise: Promise<{ data: BaseResponse<T> }>): Promise<T | undefined> {
  const { data: resp } = await promise;
  if (resp.code !== 0) {
    toast.error(resp.message);
    return undefined;
  }
  return resp.data;
}

/** 带重试的 unwrap — 用于关键请求 */
async function unwrapWithRetry<T>(
  fn: () => Promise<{ data: BaseResponse<T> }>,
): Promise<T | undefined> {
  return withRetry(() => unwrap(fn()));
}

// ---------- 上传模块 ----------

export interface BatchUploadResult {
  batch_id: string;
  batch_name: string;
  uploaded_count: number;
  failed_count: number;
  failed_files: { name: string; reason: string }[];
}

export interface BatchInfo {
  id: string;
  name: string;
  status: string;
  total_images: number;
  pending: number;
  completed: number;
  failed: number;
  create_time: string | null;
}

export interface BatchDetail {
  id: string;
  name: string;
  status: string;
  total_images: number;
  images: {
    id: string;
    filename: string;
    status: string;
    preprocess_mode: string | null;
    watermark_removed: boolean;
    retry_count: number;
    original_path: string | null;
    processed_path: string | null;
  }[];
}

/**
 * 将后端 original_path（绝对路径）转换为前端可访问的 URL。
 * 后端 StaticFiles 挂载: /api/files → DATA_DIR
 * 例: "g:\\...\\data\\uploads\\abc.jpg" → "/api/files/uploads/abc.jpg"
 */
export function toFileUrl(backendPath: string | null | undefined): string {
  if (!backendPath) return "";
  // 统一分隔符
  const normalized = backendPath.replace(/\\/g, "/");
  // 截取 data/ 之后的部分
  const marker = "/data/";
  const idx = normalized.lastIndexOf(marker);
  if (idx === -1) return "";
  return `/api/files/${normalized.slice(idx + marker.length)}`;
}

export const uploadApi = {
  /** 批量上传图片 */
  batch(files: File[], batchName: string, batchDescription?: string) {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    form.append("batch_name", batchName);
    if (batchDescription) form.append("batch_description", batchDescription);
    return unwrap<BatchUploadResult>(
      http.post(API.upload.batch, form, {
        headers: { "Content-Type": "multipart/form-data" },
      }),
    );
  },

  /** 从 URL 导入图片 */
  fromUrl(url: string, batchId?: string, batchName?: string) {
    const form = new FormData();
    form.append("url", url);
    if (batchId) form.append("batch_id", batchId);
    if (batchName) form.append("batch_name", batchName);
    return unwrap<{ batch_id: string; filename: string }>(
      http.post(API.upload.url, form, {
        headers: { "Content-Type": "multipart/form-data" },
      }),
    );
  },

  /** 获取批次列表 */
  listBatches() {
    return unwrap<{ batches: BatchInfo[] }>(http.get(API.upload.batches));
  },

  /** 获取批次详情 */
  getBatch(batchId: string) {
    return unwrap<BatchDetail>(http.get(API.upload.batchDetail(batchId)));
  },
};

// ---------- 预处理模块 ----------

export interface ProgressInfo {
  status: string;
  progress: number;
  total: number;
  completed: number;
  failed: number;
  logs: string[];
}

export const preprocessApi = {
  /** 启动预处理 */
  start(
    batchId: string,
    mode: "crop" | "expand" = "crop",
    cropOffsets?: Record<string, number>,
    imageModes?: Record<string, string>,
    expandOffsets?: Record<string, number>,
  ) {
    return unwrapWithRetry<{ batch_id: string; pending_count: number }>(() =>
      http.post(API.preprocess.start, {
        batch_id: batchId,
        mode,
        crop_offsets: cropOffsets,
        image_modes: imageModes,
        expand_offsets: expandOffsets,
      }),
    );
  },

  /** 查询预处理进度 */
  progress(batchId: string) {
    return unwrap<ProgressInfo>(http.get(API.preprocess.progress(batchId)));
  },

  /** 手动涂抹水印去除 */
  watermarkManual(imageId: string, maskData: string) {
    return http
      .post(API.preprocess.watermarkManual, { image_id: imageId, mask_data: maskData })
      .then(({ data: resp }: { data: BaseResponse }) => {
        if (resp.code !== 0) {
          toast.error(resp.message);
          return false;
        }
        return true;
      });
  },

  /** 重试单张预处理 */
  retry(imageId: string) {
    return unwrap(http.post(API.preprocess.retry(imageId)));
  },
};

// ---------- 提示词模块 ----------

export interface PromptItem {
  id: string;
  crowd_type: string;
  crowd_name: string;
  style_name: string;
  positive_prompt: string;
  negative_prompt: string;
  reference_weight: number;
  preferred_engine: string | null;
  task_count: number;
}

export interface PromptCreatePayload {
  crowd_type: string;
  style_name: string;
  positive_prompt: string;
  negative_prompt?: string;
  reference_weight?: number;
  preferred_engine?: "seedream" | "nanobanana";
  is_active?: boolean;
}

export const promptApi = {
  /** 一键生成提示词 */
  generate(batchId: string, crowdTypes?: string[], referenceImageId?: string, promptCount = 5) {
    return unwrapWithRetry<{ batch_id: string; crowd_types_count: number }>(() =>
      http.post(API.prompt.generate, {
        batch_id: batchId,
        crowd_types: crowdTypes,
        reference_image_id: referenceImageId,
        prompt_count: promptCount,
      }),
    );
  },

  /** 中断提示词生成 */
  cancel(batchId: string) {
    return unwrap(http.post(API.prompt.cancel(batchId)));
  },

  /** 查询生成进度 */
  progress(batchId: string) {
    return unwrap<ProgressInfo>(http.get(API.prompt.progress(batchId)));
  },

  /** 获取提示词列表 */
  list(params?: { batch_id?: string; crowd_type?: string }) {
    return unwrap<{ prompts: PromptItem[]; total: number }>(
      http.get(API.prompt.list, { params }),
    );
  },

  /** 编辑提示词 */
  edit(
    promptId: string,
    data: {
      positive_prompt?: string;
      negative_prompt?: string;
      style_name?: string;
      reference_weight?: number;
      preferred_engine?: string;
    },
  ) {
    return unwrap(http.put(API.prompt.edit(promptId), null, { params: data }));
  },

  /** 删除提示词 */
  delete(promptId: string) {
    return unwrap(http.delete(API.prompt.delete(promptId)));
  },

  /** 按人群批量删除提示词 */
  deleteByCrowd(crowdType: string) {
    return unwrap<{ deleted_count: number }>(http.delete(API.prompt.deleteByCrowd(crowdType)));
  },

  /** 新增提示词 */
  create(payload: PromptCreatePayload) {
    return unwrap<{ id: string; updated: boolean }>(http.post(API.prompt.create, payload));
  },

  /** 导入提示词模板（CSV/JSON） */
  importTemplates(file: File, crowdType?: string, replaceCurrent = false) {
    const form = new FormData();
    form.append("file", file);
    if (crowdType) form.append("crowd_type", crowdType);
    form.append("replace_current", replaceCurrent ? "true" : "false");
    return unwrap<{
      created_count: number;
      updated_count: number;
      error_count: number;
      errors: string[];
      affected_crowds: string[];
    }>(
      http.post(API.prompt.import, form, {
        headers: { "Content-Type": "multipart/form-data" },
      }),
    );
  },
};

// ---------- 批量生图模块 ----------

export interface GenerateProgressInfo extends ProgressInfo {
  per_image: Record<
    string,
    { filename: string; total: number; completed: number; failed: number; progress: number }
  >;
}

export interface GenerateOverview {
  base_images: number;
  crowd_types: number;
  styles_per_type: number;
  total_tasks: number;
  completed: number;
  failed: number;
  pending: number;
  progress: number;
}

export const generateApi = {
  /** 启动批量生图 */
  start(batchId: string, engine?: string) {
    return unwrapWithRetry<{ batch_id: string; pending_count: number; engine: string }>(() =>
      http.post(API.generate.start, { batch_id: batchId, engine }),
    );
  },

  /** 查询生图进度 */
  progress(batchId: string) {
    return unwrap<GenerateProgressInfo>(http.get(API.generate.progress(batchId)));
  },

  /** 中断批量生图 */
  cancel(batchId: string) {
    return unwrap(http.post(API.generate.cancel(batchId)));
  },

  /** 重试失败任务 */
  retry(batchId: string, engine?: string) {
    return unwrapWithRetry<{ retry_count: number }>(() =>
      http.post(API.generate.retry, { batch_id: batchId, engine }),
    );
  },

  /** 获取生图概览 */
  overview(batchId: string) {
    return unwrap<GenerateOverview>(http.get(API.generate.overview(batchId)));
  },
};

// ---------- 审核分类模块 ----------

export interface ReviewItem {
  id: string;
  crowd_type: string;
  crowd_name: string;
  style_name: string;
  review_status: string;
  result_path: string | null;
  base_image_filename: string;
  batch_id: string;
  create_time: string;
}

export interface ReviewStats {
  total: number;
  pending_review: number;
  selected: number;
  pending_modification: number;
  not_selected: number;
}

export const reviewApi = {
  /** 获取审核列表 */
  list(params?: {
    batch_id?: string;
    crowd_type?: string;
    review_status?: string;
    page?: number;
    page_size?: number;
  }) {
    return unwrap<{ items: ReviewItem[]; total: number; page: number; page_size: number }>(
      http.get(API.review.list, { params }),
    );
  },

  /** 标记单张 */
  mark(taskId: string, status: "selected" | "pending_modification" | "not_selected") {
    return unwrap(http.post(API.review.mark, { task_id: taskId, status }));
  },

  /** 批量标记 */
  batchMark(taskIds: string[], status: "selected" | "pending_modification" | "not_selected") {
    return unwrap<{ updated_count: number }>(
      http.post(API.review.batchMark, { task_ids: taskIds, status }),
    );
  },

  /** 审核统计 */
  stats(batchId?: string) {
    return unwrap<ReviewStats>(http.get(API.review.stats, { params: { batch_id: batchId } }));
  },

  /** 获取生成图片 URL */
  imageUrl(taskId: string) {
    return API.review.image(taskId);
  },
};

// ---------- 模板管理模块 ----------

export interface TemplateItem {
  id: string;
  generate_task_id: string;
  crowd_type: string;
  crowd_name: string;
  style_name: string;
  original_path: string | null;
  wide_face_path: string | null;
  wide_face_status: string | null;
  compress_status: string | null;
  compressed_path: string | null;
  final_status: string;
  create_time: string;
}

export interface TemplateStats {
  total: number;
  selected: number;
  pending_modification: number;
  trash: number;
  crowd_stats: Record<string, { name: string; count: number }>;
}

export interface TemplateUploadResult {
  items: TemplateItem[];
  uploaded_count: number;
  failed_count: number;
  failed_files: { name: string; reason: string }[];
}

export const templateApi = {
  /** 获取模板列表 */
  list(params?: {
    crowd_type?: string;
    final_status?: string;
    page?: number;
    page_size?: number;
  }) {
    return unwrap<{ items: TemplateItem[]; total: number; page: number; page_size: number }>(
      http.get(API.template.list, { params }),
    );
  },

  /** 上传模板图到选用库 */
  upload(files: File[], crowdType: string) {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    form.append("crowd_type", crowdType);
    return unwrap<TemplateUploadResult>(
      http.post(API.template.upload, form, {
        headers: { "Content-Type": "multipart/form-data" },
      }),
    );
  },

  /** 替换模板图（原图/宽脸图） */
  replace(templateId: string, file: File, isWideFace = false) {
    const form = new FormData();
    form.append("file", file);
    form.append("is_wide_face", String(isWideFace));
    return unwrap<{ item: TemplateItem }>(
      http.post(API.template.replace(templateId), form, {
        headers: { "Content-Type": "multipart/form-data" },
      }),
    );
  },

  /** 移动模板到指定库 */
  move(templateId: string, target: "selected" | "pending_modification" | "trash") {
    return unwrap(http.post(API.template.move, { template_id: templateId, target }));
  },

  /** 批量移动 */
  batchMove(templateIds: string[], target: "selected" | "pending_modification" | "trash") {
    return unwrap<{ updated_count: number }>(
      http.post(API.template.batchMove, { template_ids: templateIds, target }),
    );
  },

  /** 永久删除（仅回收站） */
  delete(templateId: string) {
    return unwrap(http.delete(API.template.delete(templateId)));
  },

  /** 统计数据 */
  stats(crowdType?: string) {
    return unwrap<TemplateStats>(
      http.get(API.template.stats, { params: { crowd_type: crowdType } }),
    );
  },

  /** 获取模板图片 URL */
  imageUrl(templateId: string) {
    return API.template.image(templateId);
  },
};

// ---------- 宽脸图模块 ----------

export const widefaceApi = {
  /** 批量生成宽脸图 */
  generate(templateIds: string[], engine?: string) {
    return unwrapWithRetry(() =>
      http.post(API.wideface.generate, { template_ids: templateIds, engine }),
    );
  },

  /** 查询宽脸图生成进度 */
  progress() {
    return unwrap<ProgressInfo>(http.get(API.wideface.progress));
  },

  /** 中断宽脸图生成 */
  cancel() {
    return unwrap(http.post(API.wideface.cancel));
  },

  /** 宽脸图审核 */
  review(templateId: string, status: "pass" | "regenerate") {
    return unwrap(
      http.post(API.wideface.review, { template_id: templateId, status }),
    );
  },
};

// ---------- 画质压缩模块 ----------

export const compressApi = {
  /** 启动压缩 */
  start(targetSizeKb?: number, minQuality?: number, maxQuality?: number) {
    return unwrapWithRetry(() =>
      http.post(API.compress.start, {
        target_size_kb: targetSizeKb,
        min_quality: minQuality,
        max_quality: maxQuality,
      }),
    );
  },

  /** 查询压缩进度 */
  progress() {
    return unwrap<ProgressInfo>(http.get(API.compress.progress));
  },

  /** 重试压缩 */
  retry(imageId: string) {
    return unwrap(http.post(API.compress.retry(imageId)));
  },
};

// ---------- 批量导出模块 ----------

export const exportApi = {
  /** 启动导出 */
  start(exportDir?: string) {
    return unwrapWithRetry(() =>
      http.post(API.export.start, { export_dir: exportDir }),
    );
  },

  /** 查询导出进度 */
  progress() {
    return unwrap<ProgressInfo>(http.get(API.export.progress));
  },
};

// ---------- 系统设置模块 ----------

export interface SettingValue {
  value: string;
  description: string;
}

export const settingsApi = {
  /** 获取全部设置（API Key 掩码） */
  list() {
    return unwrap<Record<string, SettingValue>>(http.get(API.settings.list));
  },

  /** 获取全部设置（API Key 明文，内部用） */
  raw() {
    return unwrap<Record<string, SettingValue>>(http.get(API.settings.raw));
  },

  /** 批量更新设置 */
  update(settings: { key: string; value: string }[]) {
    return unwrap(http.post(API.settings.update, { settings }));
  },

  /** 测试 API 连接 */
  testConnection(service: "bailian" | "apiyi", apiKey: string) {
    return unwrap<{ connected: boolean }>(
      http.post(API.settings.testConnection, { service, api_key: apiKey }),
    );
  },
};

// ---------- 导出 axios 实例（特殊场景用） ----------

export { http };
