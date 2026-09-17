<template>
  <view class="page">
    <text class="eyebrow">LEGAL</text>
    <text class="title">关于与隐私政策</text>
    <text class="muted">论文卫士Pro 小程序 · 隐私说明版本 {{ version }}</text>

    <view class="panel">
      <text class="panel-title">我们会处理什么</text>
      <text v-for="item in collection" :key="item" class="item">{{ item }}</text>
    </view>

    <view class="panel">
      <text class="panel-title">我们不会采集</text>
      <text v-for="item in notCollected" :key="item" class="item">{{ item }}</text>
    </view>

    <view class="panel">
      <text class="panel-title">分享与正文</text>
      <text class="copy">{{ summary }}</text>
    </view>

    <view class="panel">
      <text class="panel-title">关于本程序</text>
      <text class="copy">论文卫士Pro 用于校内论文过程管理。长篇写作、论文绑定与学院管理请使用电脑端。本页不展示密钥或个人身份详情。</text>
    </view>
  </view>
</template>

<script setup lang="ts">
import { onLoad } from '@dcloudio/uni-app'
import {
  PRIVACY_COLLECTION_SCOPE,
  PRIVACY_NOT_COLLECTED,
  PRIVACY_VERSION,
  applySharePolicy,
  privacyNoticeSummary,
} from '../../utils/privacy'

const version = PRIVACY_VERSION
const collection = PRIVACY_COLLECTION_SCOPE
const notCollected = PRIVACY_NOT_COLLECTED
const summary = privacyNoticeSummary()

onLoad(() => {
  applySharePolicy('/pages/profile/about')
})
</script>

<style scoped>
.page { min-height: 100vh; box-sizing: border-box; padding: 48rpx 36rpx 80rpx; }
.eyebrow { display: block; color: #6b7280; font-size: 22rpx; letter-spacing: 3rpx; }
.title { display: block; margin-top: 8rpx; color: #111827; font-size: 50rpx; font-weight: 800; }
.muted { display: block; margin-top: 12rpx; color: #6b7280; font-size: 24rpx; }
.panel { margin-top: 28rpx; padding: 30rpx; background: #fff; border: 1rpx solid #e5e7eb; border-radius: 20rpx; }
.panel-title { display: block; color: #111827; font-size: 29rpx; font-weight: 750; }
.item, .copy { display: block; margin-top: 16rpx; color: #374151; font-size: 25rpx; line-height: 1.65; }
</style>
