import { useAuth } from './AuthContext';

/**
 * Renders children only when the current user holds `permission`.
 * Renders `fallback` (default: null) otherwise.
 *
 * Usage:
 *   <RequirePermission permission="trigger:backup">
 *     <BackupButton />
 *   </RequirePermission>
 *
 *   <RequirePermission permission="manage:users" fallback={<span>No access</span>}>
 *     <UserTable />
 *   </RequirePermission>
 */
export default function RequirePermission({ permission, fallback = null, children }) {
  const { hasPermission } = useAuth();
  return hasPermission(permission) ? children : fallback;
}
