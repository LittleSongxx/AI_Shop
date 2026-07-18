import { openAlipayPagePay } from '@/utils/alipayPagePay';


export { openAlipayPagePay };


export function openAlipayWapPay(payInfoHtml: string): boolean {
  if (!payInfoHtml?.trim()) return false;
  const wrap = document.createElement('div');
  wrap.style.display = 'none';
  wrap.innerHTML = payInfoHtml;
  document.body.appendChild(wrap);
  const form =
    (wrap.querySelector('form[name="punchout_form"]') as HTMLFormElement | null) ||
    (wrap.querySelector('form') as HTMLFormElement | null);
  if (!form) {
    wrap.remove();
    return false;
  }
  form.submit();
  return true;
}

export function launchAlipayPay(payInfoHtml: string, isMobile: boolean): boolean {
  return isMobile ? openAlipayWapPay(payInfoHtml) : openAlipayPagePay(payInfoHtml);
}
