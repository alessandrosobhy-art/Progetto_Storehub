# fix_app.py
import re, ast, sys, shutil
from pathlib import Path

APP = Path("app.py")
BACK = Path("app.py.bak")

def read(p): return p.read_text(encoding="utf-8")
def write(p,s): p.write_text(s, encoding="utf-8")

def replace_admin_supervisors(src: str) -> str:
    patt = r"@app\.get\('/admin/supervisors'\)[\s\S]*?def\s+admin_supervisors\s*\([\s\S]*?return render_template\([\s\S]*?\)\n"
    repl = r"""@app.get('/admin/supervisors')
@login_required
@admin_required
def admin_supervisors():
    guard = _require_login()
    if guard:
        return guard
    token = session.get('sb_token')

    # Optional filter by supervisor_id (?supervisor_id=...)
    sup_filter = request.args.get('supervisor_id') or ''

    # supervisors list
    r_sup = _sb_get('profiles', token, params={
        'select': 'id,name,email,role',
        'role': 'eq.supervisor',
        'order': 'email.asc'
    })
    supervisors = r_sup.json() if r_sup.ok else []

    # users list (all)
    r_users = _sb_get('profiles', token, params={
        'select': 'id,name,email,role',
        'order': 'email.asc'
    })
    users = r_users.json() if r_users.ok else []

    # assignments
    params = {
        'select': (
            'supervisor_id,user_id,can_edit,'
            'supervisor:supervisor_id(id,email,name),'
            'user:user_id(id,email,name)'
        )
    }
    if sup_filter:
        params['supervisor_id'] = f'eq.{sup_filter}'
    r_asg = _sb_get('supervisor_assignments', token, params=params)

    assignments = []
    if r_asg.ok:
        for row in (r_asg.json() or []):
            assignments.append({
                'supervisor_id': row.get('supervisor_id'),
                'user_id': row.get('user_id'),
                'can_edit': bool(row.get('can_edit', False)),
                'supervisor_email': (row.get('supervisor') or {}).get('email'),
                'supervisor_name': (row.get('supervisor') or {}).get('name'),
                'user_email': (row.get('user') or {}).get('email'),
                'user_name': (row.get('user') or {}).get('name'),
            })

    return render_template(
        'admin_supervisors.html',
        supervisors=supervisors,
        users=users,
        assignments=assignments,
        sup_filter=sup_filter
    )
"""
    new, n = re.subn(patt, repl, src, flags=re.DOTALL)
    if n == 0:
        print("ATTENZIONE: non ho trovato la funzione admin_supervisors() da sostituire (decorator mancante?).")
    return new

def fix_calendar_users_tail(src: str) -> str:
    # cerchiamo la funzione e sostituiamo da "Compute can_edit flags" fino al return
    start = src.find("def calendar_users():")
    if start == -1:
        print("ATTENZIONE: non ho trovato def calendar_users().")
        return src
    # tagliamo il blocco funzione
    end = src.find("@app", start+1)
    if end == -1:
        end = len(src)
    block = src[start:end]

    if "# Compute can_edit flags" not in block:
        print("Nota: in calendar_users() non ho trovato il marcatore '# Compute can_edit flags'. Inserisco la coda corretta.")
        # Proviamo a inserire prima del return finale (jsonify)
        patt_return = r"return\s+jsonify\(\{\s*'users'\s*:\s*users\s*\}\)"
        if re.search(patt_return, block):
            block = re.sub(patt_return, r"""
    # Compute can_edit flags
    try:
        role = my_role
        if role == 'admin':
            for u in users:
                u['can_edit'] = True
        else:
            for u in users:
                u['can_edit'] = (u.get('id') == my_id)
            if role == 'supervisor':
                r_asg = _sb_get('supervisor_assignments', token, params={
                    'select': 'user_id,can_edit',
                    'supervisor_id': f'eq.{my_id}'
                })
                if r_asg.ok:
                    by_id = {u.get('id'): u for u in users if u.get('id')}
                    for a in (r_asg.json() or []):
                        uid = a.get('user_id'); ce = a.get('can_edit', False)
                        if uid in by_id:
                            by_id[uid]['can_edit'] = bool(ce)
    except Exception as _e:
        app.logger.warning('can_edit compute failed: %s', _e)

    if my_id:
        users.sort(key=lambda u: (0 if u.get('id') == my_id else 1, (u.get('email') or u.get('name') or '').lower()))
    return jsonify({'users': users})
""", block)
        else:
            print("ATTENZIONE: non ho trovato il return jsonify in calendar_users(). Lascio invariato.")
    else:
        block = re.sub(r"# Compute can_edit flags[\s\S]*?return\s+jsonify\(\{\s*'users'\s*:\s*users\s*\}\)",
r"""# Compute can_edit flags
    try:
        role = my_role
        if role == 'admin':
            for u in users:
                u['can_edit'] = True
        else:
            for u in users:
                u['can_edit'] = (u.get('id') == my_id)
            if role == 'supervisor':
                r_asg = _sb_get('supervisor_assignments', token, params={
                    'select': 'user_id,can_edit',
                    'supervisor_id': f'eq.{my_id}'
                })
                if r_asg.ok:
                    by_id = {u.get('id'): u for u in users if u.get('id')}
                    for a in (r_asg.json() or []):
                        uid = a.get('user_id'); ce = a.get('can_edit', False)
                        if uid in by_id:
                            by_id[uid]['can_edit'] = bool(ce)
    except Exception as _e:
        app.logger.warning('can_edit compute failed: %s', _e)

    if my_id:
        users.sort(key=lambda u: (0 if u.get('id') == my_id else 1, (u.get('email') or u.get('name') or '').lower()))
    return jsonify({'users': users})""", block, flags=re.DOTALL)

    return src[:start] + block + src[end:]

def strip_ellipses(src: str) -> str:
    # Rimuovi ellissi che spezzano la sintassi
    src = src.replace("\n...\n", "\n# ...\n")
    src = src.replace("\n...\r\n", "\n# ...\r\n")
    src = src.replace("…", "# …")
    return src

def main():
    if not APP.exists():
        print("Non trovo app.py nella cartella corrente.")
        sys.exit(1)

    # backup
    shutil.copyfile(APP, BACK)
    print("Backup creato:", BACK)

    src = read(APP)
    src = strip_ellipses(src)
    src = replace_admin_supervisors(src)
    src = fix_calendar_users_tail(src)

    # Validazione sintattica
    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"ERRORE SINTASSI dopo patch: {e.msg} (linea {e.lineno}, col {e.offset})")
        # Mostra qualche riga intorno
        lines = src.splitlines()
        L = e.lineno or 1
        lo = max(1, L-3); hi = min(len(lines), L+3)
        for i in range(lo, hi+1):
            print(f"{i:04d}: {lines[i-1]}")
        print("\nHo lasciato il backup in", BACK)
        sys.exit(2)

    write(APP, src)
    print("Patch applicata con successo a app.py (sintassi OK).")

if __name__ == "__main__":
    main()
