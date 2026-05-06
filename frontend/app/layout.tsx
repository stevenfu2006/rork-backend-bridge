import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Rork Backend Bridge',
  description: 'Generate a deployable backend from a plain-English app description',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ margin: 0, padding: 0, backgroundColor: '#f8fafc', fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, sans-serif' }}>
        {children}
      </body>
    </html>
  );
}
