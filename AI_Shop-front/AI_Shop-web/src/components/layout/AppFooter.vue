<template>
  <footer class="app-footer ignore">
    <div class="footer-inner">
      <p class="copyright-line">
        <a class="beian-link" href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer">闽ICP备2026020850号</a>
      </p>
      <p class="copyright-text">© 2026 智选 SmartSelect 版权所有</p>
    </div>
  </footer>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.app-footer.ignore {
  background: $color-footer-bg;
  border-top: 1px solid $color-border-gray;
  margin-top: 24px;

  .footer-inner {
    max-width: $content-width;
    margin: 0 auto;
    min-height: 48px;
    padding: 8px 16px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 12px;
  }

  @media (max-width: $breakpoint-mobile) {
    background: transparent;
    border-top: none;
    margin-top: 0;
    padding-bottom: calc($mobile-tab-reserved + 56px);

    .footer-inner {
      height: auto;
      padding: 12px 16px;
      flex-direction: column;
      gap: 4px;
    }
  }

  .copyright-line,
  .copyright-text {
    margin: 0;
    font-size: 12px;
    line-height: 1.4;
    color: $color-text-muted;
    text-align: center;
  }

  .copyright-line {
    white-space: nowrap;
  }

  .beian-link {
    font-size: 12px;
    line-height: 1.4;
    color: $color-text-muted;
    text-decoration: none;
    transition: color $transition-fast;

    &:hover {
      color: $color-primary;
      text-decoration: underline;
    }
  }
}
</style>
