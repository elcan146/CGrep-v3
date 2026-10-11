"""
seed_rbac.py — Bootstrap RBAC roles, permissions, and assign SuperAdmin to admin.

Run once:
    python seed_rbac.py

Safe to re-run — uses get_or_create logic, never duplicates.
"""

import os, sys

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.env')
if os.path.isfile(_CONFIG_PATH):
    with open(_CONFIG_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                os.environ.setdefault(k.strip(), v.strip())

from models import SessionLocal, User, Role, Permission

# ---------------------------------------------------------------------------
# Permission catalogue
# ---------------------------------------------------------------------------
PERMISSIONS = [
    'view:dashboard',
    'view:history',
    'view:telemetry',
    'export:data',
    'trigger:backup',
    'manage:users',
    'manage:rbac',
    'admin:logout-all',
    'view:audit_logs',
]

# ---------------------------------------------------------------------------
# Role catalogue  (priority_level: higher = more powerful)
# ---------------------------------------------------------------------------
ROLES = [
    {'name': 'SuperAdmin', 'priority_level': 100, 'permissions': PERMISSIONS},
    {'name': 'Operator',   'priority_level': 10,  'permissions': [
        'view:dashboard', 'view:history', 'view:telemetry',
    ]},
    {'name': 'Analyst',    'priority_level': 20,  'permissions': [
        'view:dashboard', 'view:history', 'view:telemetry', 'export:data',
    ]},
]

ADMIN_USERNAME = 'admin'


def get_or_create_permission(db, name: str) -> Permission:
    p = db.query(Permission).filter(Permission.name == name).first()
    if not p:
        p = Permission(name=name)
        db.add(p)
        db.flush()
        print(f'  [+] Permission: {name}')
    return p


def get_or_create_role(db, name: str, priority_level: int) -> Role:
    r = db.query(Role).filter(Role.name == name).first()
    if not r:
        r = Role(name=name, priority_level=priority_level)
        db.add(r)
        db.flush()
        print(f'  [+] Role: {name} (priority={priority_level})')
    else:
        r.priority_level = priority_level
    return r


def main():
    db = SessionLocal()
    try:
        print('\n=== cgrep RBAC Seed ===\n')

        print('[1] Creating permissions...')
        perm_map: dict[str, Permission] = {}
        for pname in PERMISSIONS:
            perm_map[pname] = get_or_create_permission(db, pname)
        db.flush()

        print('\n[2] Creating roles and wiring permissions...')
        for rdef in ROLES:
            role = get_or_create_role(db, rdef['name'], rdef['priority_level'])
            role.permissions = [perm_map[p] for p in rdef['permissions']]

        db.flush()

        print(f'\n[3] Assigning SuperAdmin to "{ADMIN_USERNAME}"...')
        admin_user = db.query(User).filter(User.username == ADMIN_USERNAME).first()
        if not admin_user:
            print(f'  [!] User "{ADMIN_USERNAME}" not found — skipping role assignment.')
            print(f'      Create the user first with seed_users.py, then re-run this script.')
        else:
            super_role = db.query(Role).filter(Role.name == 'SuperAdmin').first()
            if super_role not in admin_user.roles:
                admin_user.roles.append(super_role)
                print(f'  [+] SuperAdmin assigned to {ADMIN_USERNAME}')
            else:
                print(f'  [=] {ADMIN_USERNAME} already has SuperAdmin')

        db.commit()
        print('\n=== Done ===')
        print(f'  Permissions : {len(PERMISSIONS)}')
        print(f'  Roles       : {len(ROLES)}')

    except Exception as e:
        db.rollback()
        print(f'\n[ERROR] {e}')
        sys.exit(1)
    finally:
        db.close()


if __name__ == '__main__':
    main()
