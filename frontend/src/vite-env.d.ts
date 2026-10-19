/// <reference types="vite/client" />

// ant-design-vue 的全局样式文件没有类型声明
declare module 'ant-design-vue/dist/reset.css';

// AMap JSAPI loader 注入的全局命名空间
interface Window {
  _AMapSecurityConfig?: { securityJsCode?: string }
}
