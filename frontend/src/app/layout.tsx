import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Anomaly Explainer',
  description: 'AI-powered payment TPV anomaly detection and root cause analysis',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
