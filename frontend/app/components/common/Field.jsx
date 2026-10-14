export function Field({ label, hint, children }) {
  return (
    <label className="field">
      {label}
      {hint && <span className="hint">{hint}</span>}
      {children}
    </label>
  )
}
