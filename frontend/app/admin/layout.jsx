import { AdminGate } from '../components/admin/AdminGate.jsx'
import { Header } from '../components/common/Header.jsx'

export default function AdminLayout({ children }) {
  return (
    <>
      <Header />
      <AdminGate>{children}</AdminGate>
    </>
  )
}
