import { ElMessageBox } from 'element-plus'

const ios26MessageBoxOptions = {
  closeOnClickModal: false,
  distinguishCancelAndClose: true,
  autofocus: false,
  center: true,
}


export async function promptAdminConfirmPassword(message = '此操作为敏感操作，请输入管理员密码确认') {
  const { value } = await ElMessageBox.prompt(message, '安全确认', {
    ...ios26MessageBoxOptions,
    inputType: 'password',
    inputPlaceholder: '管理员密码',
    confirmButtonText: '确认',
    cancelButtonText: '取消',
  })
  if (!value) {
    throw new Error('cancel')
  }
  return value
}


export const ConfirmSensitive = ({ message, okfun }) => {
  ElMessageBox.confirm(message, '提示', {
    ...ios26MessageBoxOptions,
    confirmButtonText: '继续',
    cancelButtonText: '取消',
  })
    .then(async () => {
      const confirmPwd = await promptAdminConfirmPassword()
      if (okfun) {
        await okfun(confirmPwd)
      }
    })
    .catch(() => {})
}

export { ConfirmSensitive as default }
