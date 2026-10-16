// 浏览器端地图 AK 的来源有三层，从优先到兜底：
//   1) 运行时注入的 /env.js（部署后改 .env 就生效，不用重新构建镜像）
//   2) 构建期 --build-arg VITE_BMAP_AK（本地 dev 走 frontend/.env.local）
//   3) 空 → 无密钥加载，地图能出但瓦片会鉴权失败（只在本地 demo 能忍）
const AK = (window.__CIRCLE15__ && window.__CIRCLE15__.bmapAk) || import.meta.env.VITE_BMAP_AK || ''
let loading = null

/** 动态加载 BMapGL，AK 留空时走无密钥加载（本地 demo）。 */
export function loadBmapGL() {
  if (window.BMapGL) return Promise.resolve(window.BMapGL)
  if (loading) return loading
  loading = new Promise((resolve, reject) => {
    window.__bmapReady = () => resolve(window.BMapGL)
    const s = document.createElement('script')
    s.src = `https://api.map.baidu.com/api?type=webgl&v=1.0&ak=${encodeURIComponent(AK)}&callback=__bmapReady`
    s.onerror = () => reject(new Error('BMapGL 加载失败，检查 AK 与网络'))
    document.head.appendChild(s)
  })
  return loading
}
