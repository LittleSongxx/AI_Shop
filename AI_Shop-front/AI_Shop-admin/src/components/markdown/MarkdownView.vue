<template>
  <div class="md-preview-panel" v-if="modelValue">
    <div class="md-preview">
      <MdPreview :noEcharts="true" previewTheme="vuepress" :codeFoldable="false" id="preview" :modelValue="modelValue"
        noMermaid />
    </div>
  </div>
</template>
<script setup>


import { MdPreview, MdCatalog, config } from 'md-editor-v3'
import 'md-editor-v3/lib/style.css'

import screenfull from 'screenfull'

import highlight from 'highlight.js'
import 'highlight.js/styles/atom-one-dark.css'

import * as prettier from 'prettier'
import parserMarkdown from 'prettier/plugins/markdown'

import katex from 'katex'

import LinkAttr from 'markdown-it-link-attributes'
config({
  editorExtensions: {
    prettier: {
      prettierInstance: prettier,
      parserMarkdownInstance: parserMarkdown,
    },
    highlight: {
      instance: highlight,
    },
    screenfull: {
      instance: screenfull,
    },
    katex: {
      instance: katex,
    },
  },
  markdownItPlugins(plugins) {
    return [
      ...plugins,
      {
        type: 'linkAttr',
        plugin: LinkAttr,
        options: {
          matcher(href) {
            
            
            return !href.startsWith('#')
          },
          attrs: {
            target: '_blank',
          },
        },
      },
    ]
  },
})
import {
  createApp,
  ref,
  reactive,
  getCurrentInstance,
  nextTick,
  onMounted,
  computed,
} from 'vue'
const { proxy } = getCurrentInstance()
import { useRoute, useRouter } from 'vue-router'
const route = useRoute()
const router = useRouter()
const props = defineProps({
  modelValue: {
    type: String,
  },
})
const scrollElement = document.documentElement
</script>

<style lang="scss" scoped>
.md-preview-panel {
  background: #f0f2f5;
  display: flex;
  .md-preview {
    flex: 1;
    :deep(.md-editor-preview-wrapper) {
      padding: 10px;
    }
    :deep(.md-editor-preview) {
      background: #f0f2f5;
      .md-editor-code {
        .md-editor-code-head {
          z-index: 1;
        }
        pre {
          code {
            .md-editor-code-block {
              word-wrap: break-word;
              white-space: break-spaces;
            }
          }
        }
      }
      img {
        max-width: 100%;
      }
      blockquote {
        border-left: 3px solid var(--text3);
        padding-left: 10px;
        p {
          color: var(--text3);
        }
      }
      a {
        color: var(--link2);
      }
      h1,
      h2,
      h3,
      h4,
      h5,
      h6 {
        margin: 10px 0px 5px 0px;
      }
      h1 {
        font-size: 26px;
      }
      h2 {
        font-size: 22px;
      }
      h3 {
        font-size: 20px;
      }
      h4 {
        font-size: 18px;
      }
      h5 {
        font-size: 16px;
      }
      h6 {
        font-size: 14px;
      }
      a {
        color: var(--link2);
      }
    }
  }
}
</style>
