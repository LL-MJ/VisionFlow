// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from '../src/App.vue'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('后端连接状态', () => {
  it('健康接口正常时显示已连接', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = mount(App)

    await flushPromises()

    expect(fetchMock).toHaveBeenCalledWith('/health')
    expect(wrapper.text()).toContain('后端已连接')
    wrapper.unmount()
  })

  it('健康接口请求失败时提示未连接', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network error')))
    const wrapper = mount(App)

    await flushPromises()

    expect(wrapper.text()).toContain('后端未连接')
    wrapper.unmount()
  })

  it('健康接口返回错误状态时提示未连接', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }))
    const wrapper = mount(App)

    await flushPromises()

    expect(wrapper.text()).toContain('后端未连接')
    wrapper.unmount()
  })
})
