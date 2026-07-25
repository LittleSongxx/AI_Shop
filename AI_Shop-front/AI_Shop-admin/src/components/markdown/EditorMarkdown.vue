<template>
  <MdEditor :noEcharts="true" :toolbars="toolbars" :modelValue="modelValue" previewTheme="vuepress"
    :codeFoldable="false" :noPrettier="true" :showToolbarName="true" @onChange="change" @onUploadImg="onUploadImg"
    @onHtmlChanged="htmlChanged"></MdEditor>
</template>

<script setup>


import { ref, reactive, getCurrentInstance, nextTick } from 'vue'
const { proxy } = getCurrentInstance()
import { useRoute, useRouter } from 'vue-router'
const route = useRoute()
const router = useRouter()

import Request from '@/utils/Request.js'
import { Api } from '@/utils/Api.js'
import { MdEditor, config } from 'md-editor-v3'
import 'md-editor-v3/lib/style.css'

import screenfull from 'screenfull'
import highlight from 'highlight.js'
import 'highlight.js/styles/atom-one-dark.css'

import * as prettier from 'prettier'
import parserMarkdown from 'prettier/plugins/markdown'

import LinkAttr from 'markdown-it-link-attributes'

const props = defineProps({
  modelValue: {
    type: String,
    default: '',
  },
  height: {
    type: Number,
    default: 500,
  },
})
const toolbars = [
  'revoke',
  'next',
  '-',
  'bold',
  'underline',
  'italic',
  '-',
  'strikeThrough',
  'title',
  'quote',
  'unorderedList',
  'orderedList',
  'task', 
  '-',
  'image',
  'table',
  '=',
  'pageFullscreen',
  'preview',
  'previewOnly',
]

config({
  editorConfig: {
    renderDelay: 100,
  },
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
  codeMirrorExtensions(extensions) {
    return extensions.map((item) => {
      if (item.type === 'linkShortener') {
        return {
          ...item,
          options: {
            maxLength: 1000,
            shortenText: (url) => '...',
          },
        }
      }
      return item
    })
  },
})

const emit = defineEmits(['update:modelValue'])
const change = (e) => {
  emit('update:modelValue', e)
}

const textContent = ref()
const htmlChanged = (e) => {
  textContent.value = e
    .replace(/<[^>]*>/g, '') 
    .replace(/&nbsp;/gi, '')
    .replace(/\n/gi, '')
}

const getTextContent = () => {
  return textContent.value
}

defineExpose({
  getTextContent,
})

const onUploadImg = async (files, callback) => {
  const res = await Promise.all(
    files.map((file) => {
      return new Promise(async (rev, rej) => {
        const result = await Request({
          url: Api.uploadImage,
          params: {
            file: file,
          },
        })
        if (!result) {
          return
        }
        rev(Api.sourcePath + result.data)
      })
    })
  )
  callback(res)
}
</script>

<style lang="scss" scoped>
.md-editor {
  height: 100%;

  * {
    box-sizing: content-box;
  }

  :deep(p) {
    img {
      vertical-align: bottom;
      
      display: inline-block;
      margin: 0;
      padding: 0;
    }
  }

  :deep(svg.md-editor-icon) {
    
    margin: 0px auto;
  }

  :deep(.md-editor-toolbar-item-name) {
    line-height: 20px;
  }
}

div.vuepress-theme h1,
div.vuepress-theme h2,
div.vuepress-theme h3,
div.vuepress-theme h4,
div.vuepress-theme h5,
div.vuepress-theme h6 {
  margin: 0px !important;
}
</style>
