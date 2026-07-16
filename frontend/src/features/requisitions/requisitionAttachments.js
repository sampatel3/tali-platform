// Keep this list aligned with the formats the requisition attachment pipeline
// can actually read. Avoid broad `image/*`: HEIC/SVG can be selected by the
// browser but cannot be sent to the vision model.
export const REQUISITION_ATTACHMENT_ACCEPT = [
  '.txt', '.text', '.vtt', '.srt', '.md', '.markdown', '.pdf', '.docx',
  '.jpg', '.jpeg', '.png', '.gif', '.webp',
].join(',');
export const REQUISITION_ATTACHMENT_MAX_FILES = 6;
export const REQUISITION_ATTACHMENT_MAX_BYTES = 15 * 1024 * 1024;

const SAFE_ATTACHMENT_ERROR_STATUSES = new Set([413, 415, 422]);

const SUPPORTED_ATTACHMENT_EXTENSIONS = new Set([
  'txt', 'text', 'vtt', 'srt', 'md', 'markdown', 'pdf', 'docx',
  'jpg', 'jpeg', 'png', 'gif', 'webp',
]);
const SUPPORTED_ATTACHMENT_MIME_TYPES = new Set([
  'text/plain', 'text/vtt', 'text/markdown',
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'image/jpeg', 'image/png', 'image/gif', 'image/webp',
]);

const GENERIC_ATTACHMENT_MIME_TYPES = new Set(['', 'application/octet-stream']);
const TEXT_ATTACHMENT_EXTENSIONS = new Set(['txt', 'text', 'vtt', 'srt', 'md', 'markdown']);
const ALTERNATE_TEXT_ATTACHMENT_MIME_TYPES = new Set([
  'application/markdown', 'application/srt', 'application/x-subrip',
]);
const IMAGE_MIME_BY_EXTENSION = Object.freeze({
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  gif: 'image/gif',
  webp: 'image/webp',
});

const attachmentExtension = (file) => {
  const name = String(file?.name || '').toLowerCase();
  return name.includes('.') ? name.split('.').pop() : '';
};

export const isSupportedRequisitionAttachment = (file) => {
  const extension = attachmentExtension(file);
  const mimeType = String(file?.type || '').toLowerCase();
  if (!extension) return SUPPORTED_ATTACHMENT_MIME_TYPES.has(mimeType);
  if (!SUPPORTED_ATTACHMENT_EXTENSIONS.has(extension)) return false;

  // A missing/generic MIME is common in browsers, so use the allow-listed
  // extension. Concrete MIME values must agree with that extension.
  if (GENERIC_ATTACHMENT_MIME_TYPES.has(mimeType)) return true;
  if (TEXT_ATTACHMENT_EXTENSIONS.has(extension)) {
    return mimeType.startsWith('text/') || ALTERNATE_TEXT_ATTACHMENT_MIME_TYPES.has(mimeType);
  }
  if (extension === 'pdf') return mimeType === 'application/pdf';
  if (extension === 'docx') {
    return mimeType === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
  }
  return IMAGE_MIME_BY_EXTENSION[extension] === mimeType;
};

// Match the backend's six-file / 15 MB per-file guards. Rejection is
// all-or-nothing so the recruiter knows exactly which files will be sent.
export const validateRequisitionAttachments = (existing = [], incoming = []) => {
  const current = Array.from(existing || []).filter(Boolean);
  const next = Array.from(incoming || []).filter(Boolean);
  if (current.length + next.length > REQUISITION_ATTACHMENT_MAX_FILES) {
    return {
      files: [],
      error: `You can attach up to ${REQUISITION_ATTACHMENT_MAX_FILES} files per message.`,
    };
  }
  const unsupported = next.find((file) => !isSupportedRequisitionAttachment(file));
  if (unsupported) {
    return {
      files: [],
      error: `${unsupported.name || 'That file'} isn't supported. Attach a PDF, DOCX, text/Markdown file, or a JPG, PNG, GIF, or WebP image.`,
    };
  }
  const oversized = next.find((file) => Number(file?.size || 0) > REQUISITION_ATTACHMENT_MAX_BYTES);
  if (oversized) {
    return {
      files: [],
      error: `${oversized.name || 'That file'} is larger than the 15 MB per-file limit.`,
    };
  }
  return { files: next, error: '' };
};

export const isImageRequisitionAttachment = (file) => (
  Boolean(file && (file.type || '').startsWith('image/'))
);

// Attachment endpoints deliberately use plain FastAPI detail strings for
// client-correctable size/type/validation failures. Surface those exact
// messages, but do not leak arbitrary server/conflict payloads or structured
// validation internals through this public-facing helper.
export const requisitionAttachmentErrorDetail = (error, fallback) => {
  const status = Number(error?.response?.status);
  const detail = error?.response?.data?.detail;
  if (SAFE_ATTACHMENT_ERROR_STATUSES.has(status) && typeof detail === 'string' && detail.trim()) {
    return detail.trim();
  }
  return fallback;
};

let attachSeq = 0;
export const stageRequisitionAttachment = (file) => ({
  id: `att_${Date.now()}_${attachSeq++}`,
  file,
  url: isImageRequisitionAttachment(file) ? URL.createObjectURL(file) : null,
});
