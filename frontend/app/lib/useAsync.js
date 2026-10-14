'use client'
import { useCallback, useEffect, useRef, useState } from 'react'

export function useAsync(fn, deps = []) {
  const [state, setState] = useState({ data: null, error: '', loading: true })
  const fnRef = useRef(fn)
  fnRef.current = fn
  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: '' }))
    try {
      setState({ data: await fnRef.current(), error: '', loading: false })
    } catch (e) {
      setState((s) => ({ ...s, error: e.message, loading: false }))
    }
  }, [])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load() }, deps)
  return { ...state, reload: load }
}

export function useNotice() {
  const [notice, setNotice] = useState(null)
  const timer = useRef(null)
  const show = useCallback((text, tone = 'ok') => {
    setNotice({ text, tone })
    clearTimeout(timer.current)
    if (tone === 'ok') timer.current = setTimeout(() => setNotice(null), 4000)
  }, [])
  return [notice, show, () => setNotice(null)]
}
