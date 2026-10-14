import './globals.css'

export const metadata = {
  title: 'QueryMind AI',
  description: 'Ask your data in plain English',
}

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
