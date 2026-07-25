export const KNOWLEDGE_FILE_MAX_BYTES = 10 * 1024 * 1024

const ALLOWED_EXTENSIONS = new Set(['txt', 'md', 'pdf', 'docx'])

export const validateKnowledgeFile = (file) => {
  if (!file) return '请选择知识文档'
  if (!Number.isFinite(file.size) || file.size <= 0) return '知识文档不能为空'
  if (file.size > KNOWLEDGE_FILE_MAX_BYTES) return '单个知识文档不能超过10MB'
  const name = String(file.name || '').trim()
  const extension = name.includes('.') ? name.split('.').pop().toLowerCase() : ''
  if (!ALLOWED_EXTENSIONS.has(extension)) {
    return '仅支持 TXT、Markdown、PDF、DOCX 文件'
  }
  return ''
}
