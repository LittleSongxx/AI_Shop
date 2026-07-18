
export function openAlipayPagePay(payInfoHtml: string): boolean {
  if (!payInfoHtml?.trim()) return false;
  const win = window.open('', '_blank');
  if (!win) return false;
  win.document.write(payInfoHtml);
  win.document.close();
  window.setTimeout(() => {
    const form = win.document.querySelector('form[name="punchout_form"]') as HTMLFormElement | null;
    form?.submit();
  }, 0);
  return true;
}
