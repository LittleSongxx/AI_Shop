export const ADMIN_PERMISSION = Object.freeze({
  AI_EVALUATE: 'ai:evaluate',
  AI_PILOT: 'ai:pilot',
  ANALYTICS_READ: 'analytics:read',
  ANALYTICS_EXPORT: 'analytics:export',
  AUDIT_READ: 'audit:read',
})

export function normalizeAdminPrincipal(value) {
  const principal = value && typeof value === 'object' ? value : {}
  return {
    ...principal,
    roles: Array.isArray(principal.roles) ? principal.roles : [],
    permissions: Array.isArray(principal.permissions) ? principal.permissions : [],
  }
}

export function hasAdminPermission(principal, permission) {
  return normalizeAdminPrincipal(principal).permissions.includes(permission)
}

export function hasAnyAdminPermission(principal, permissions) {
  const granted = new Set(normalizeAdminPrincipal(principal).permissions)
  return permissions.some((permission) => granted.has(permission))
}
