/**
 * 集中管理所有 API 端点，方便统一修改和环境切换
 */

const API_BASE = "/api";

export const API = {
  // ===== 素材上传 =====
  upload: {
    batch: `${API_BASE}/upload/batch`,
    url: `${API_BASE}/upload/url`,
    batches: `${API_BASE}/upload/batches`,
    batchDetail: (id: string) => `${API_BASE}/upload/batch/${id}`,
  },

  // ===== 预处理 =====
  preprocess: {
    start: `${API_BASE}/preprocess/start`,
    progress: (batchId: string) => `${API_BASE}/preprocess/progress/${batchId}`,
    watermarkManual: `${API_BASE}/preprocess/watermark/manual`,
    retry: (imageId: string) => `${API_BASE}/preprocess/retry/${imageId}`,
  },

  // ===== 提示词 =====
  prompt: {
    generate: `${API_BASE}/prompt/generate`,
    progress: (batchId: string) => `${API_BASE}/prompt/progress/${batchId}`,
    cancel: (batchId: string) => `${API_BASE}/prompt/cancel/${batchId}`,
    list: `${API_BASE}/prompt/list`,
    edit: (id: string) => `${API_BASE}/prompt/edit/${id}`,
    delete: (id: string) => `${API_BASE}/prompt/delete/${id}`,
  },

  // ===== 批量生图 =====
  generate: {
    start: `${API_BASE}/generate/start`,
    progress: (batchId: string) => `${API_BASE}/generate/progress/${batchId}`,
    cancel: (batchId: string) => `${API_BASE}/generate/cancel/${batchId}`,
    retry: `${API_BASE}/generate/retry`,
    overview: (batchId: string) => `${API_BASE}/generate/overview/${batchId}`,
  },

  // ===== 审核分类 =====
  review: {
    list: `${API_BASE}/review/list`,
    mark: `${API_BASE}/review/mark`,
    batchMark: `${API_BASE}/review/batch-mark`,
    stats: `${API_BASE}/review/stats`,
    image: (taskId: string) => `${API_BASE}/review/image/${taskId}`,
  },

  // ===== 模板管理 =====
  template: {
    list: `${API_BASE}/template/list`,
    upload: `${API_BASE}/template/upload`,
    replace: (id: string) => `${API_BASE}/template/replace/${id}`,
    move: `${API_BASE}/template/move`,
    batchMove: `${API_BASE}/template/batch-move`,
    delete: (id: string) => `${API_BASE}/template/delete/${id}`,
    stats: `${API_BASE}/template/stats`,
    image: (id: string) => `${API_BASE}/template/image/${id}`,
  },

  // ===== 宽脸图 =====
  wideface: {
    generate: `${API_BASE}/wideface/generate`,
    progress: `${API_BASE}/wideface/progress`,
    cancel: `${API_BASE}/wideface/cancel`,
    review: `${API_BASE}/wideface/review`,
  },

  // ===== 画质压缩 =====
  compress: {
    start: `${API_BASE}/compress/start`,
    progress: `${API_BASE}/compress/progress`,
    retry: (imageId: string) => `${API_BASE}/compress/retry/${imageId}`,
  },

  // ===== 批量导出 =====
  export: {
    start: `${API_BASE}/export/start`,
    progress: `${API_BASE}/export/progress`,
  },

  // ===== 系统设置 =====
  settings: {
    list: `${API_BASE}/settings/`,
    raw: `${API_BASE}/settings/raw`,
    update: `${API_BASE}/settings/update`,
    testConnection: `${API_BASE}/settings/test-connection`,
  },
} as const;
