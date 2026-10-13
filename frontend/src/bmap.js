const AK = import.meta.env.VITE_BMAP_AK || ''
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
