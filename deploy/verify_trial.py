# Read-only check of the testers' findings on the live database, run at the end of each deploy (odoo-bin shell).
# Each check acts as a TEST account inside a savepoint; nothing is ever committed: the whole run is rolled back.
# Output lines: "VERIFY <id> PASS|FAIL|SKIP <detail>", read with DIAGNOSE=1 or in the deploy log.
from odoo import fields

results = []


def user(login):
    return env['res.users'].sudo().search([('login', '=', login)], limit=1)


class _Undo(Exception):
    """Raised after a check succeeds, so its savepoint is rolled back too."""


def check(code, label, fn, expect_error=None):
    """fn returns a short detail. With expect_error, the check passes when that error (class name) is raised.
    Every check is undone before the next one, whatever happened."""
    status = detail = None
    try:
        with env.cr.savepoint():
            detail = fn()
            raise _Undo()
    except _Undo:
        status = 'FAIL' if expect_error else 'PASS'
        if expect_error:
            detail = f"no {expect_error} raised ({detail})"
    except Skip as e:
        status, detail = 'SKIP', str(e)
    except Exception as e:  # noqa: BLE001 - every outcome is reported
        name = type(e).__name__
        status = 'PASS' if expect_error and name == expect_error else 'FAIL'
        detail = f"{name}: {str(e).splitlines()[0][:160]}"
    env.invalidate_all()
    results.append(status)
    print(f"VERIFY {code} {status} {label} :: {detail}")


class Skip(Exception):
    pass


def need(record, what):
    if not record:
        raise Skip(f"{what} not found")
    return record


# P1 HR officer cannot move an employee out of their departments
def p1():
    hro = need(user('hr.officer'), 'hr.officer')
    emp = need(env['hr.employee'].search([('department_id', 'in', hro.alfia_department_ids.ids)], limit=1), 'employee in scope')
    other = need(env['hr.department'].search([('id', 'not in', hro.alfia_department_ids.ids)], limit=1), 'other department')
    emp.with_user(hro).write({'department_id': other.id})
    return 'saved'


check('P1', 'hr.officer moving an employee out of scope is refused', p1, expect_error='UserError')

mare = env['alfia.horse'].search([('name', '=', 'TEST E2 service mare')], limit=1)


def p2a():
    bm = need(user('breeding.manager'), 'breeding.manager')
    move = env['alfia.horse.movement'].with_user(bm).create({'horse_id': need(mare, 'TEST E2 service mare').id, 'kind': 'departure',
                                                             'reason': 'VERIFY'})
    move.with_user(bm).action_done()
    return move.state


def p2b():
    tm = need(user('training.manager'), 'training.manager')
    env['alfia.horse.movement'].with_user(tm).create({'horse_id': need(mare, 'TEST E2 service mare').id, 'kind': 'departure',
                                                      'reason': 'VERIFY'})
    return 'created'


check('P2a', 'breeding.manager records a departure in their sites', p2a)
check('P2b', 'training.manager outside their sites gets a message naming the place', p2b, expect_error='AccessError')


def p3():
    pa = need(user('purchase.approver'), 'purchase.approver')
    bill = need(env['account.move'].search([('move_type', '=', 'in_invoice')], limit=1), 'a supplier bill')
    data = bill.with_user(pa).read(['name', 'amount_total', 'invoice_line_ids', 'alfia_price_variance'])
    return f"{data[0]['name']} readable"


def p3b():
    pa = need(user('purchase.approver'), 'purchase.approver')
    bill = need(env['account.move'].search([('move_type', '=', 'in_invoice'), ('state', '=', 'draft')], limit=1)
                or env['account.move'].search([('move_type', '=', 'in_invoice')], limit=1), 'a supplier bill')
    bill.with_user(pa).write({'ref': 'VERIFY'})
    return 'written'


check('P3a', 'purchase.approver opens supplier bills', p3)
check('P3b', 'purchase.approver cannot edit them', p3b, expect_error='AccessError')


def p4():
    bm = need(user('breeding.manager'), 'breeding.manager')
    plans = env['alfia.charge.plan'].with_user(bm).search([])
    plans.read(['display_name'])
    return f"{len(plans)} of {env['alfia.charge.plan'].search_count([])} plans readable"


check('P4', 'breeding.manager opens recurring charges', p4)


def p5():
    groom = need(user('groom.a'), 'groom.a')
    wh = env['stock.warehouse'].search([], limit=1)
    feed = need(env['product.product'].search([('name', '=', 'TEST R10 feed (kg)')], limit=1), 'TEST R10 feed')
    issue = env['alfia.feed.issue'].with_user(groom).with_context(alfia_verify=True).new(
        {'product_id': feed.id, 'location_id': wh.lot_stock_id.id})
    issue.with_user(groom)._alfia_check_store()          # what saving checks, without using a feed issue number
    return 'allowed'


check('P5', 'groom.a requests feed from the store', p5)


def p6():
    names = [login for login in ('stock.controller', 'stock.controller2')
             if user(login).has_group('alfia_security.group_alfia_stock_adjust_approver')]
    if len(names) < 2:
        raise ValueError(f"approvers found: {names}")
    return ', '.join(names)


check('P6', 'two stock-count approvers exist', p6)


def p8():
    view = env['ir.ui.view'].sudo().search([('model', '=', 'alfia.asset'), ('type', '=', 'form')], limit=1)
    arch = env['alfia.asset'].with_context(lang='ar_001').get_views([(view.id, 'form')])['views']['form']['arch'] if view else ''
    if not arch:
        raise Skip('asset form not found')
    missing = [w for w in ('Send to maintenance', 'Retire') if f'string="{w}"' in arch]
    if missing:
        raise ValueError(f"still English: {missing}")
    return 'asset buttons in Arabic'


check('P8', 'Arabic labels on the asset screen', p8)


def p9():
    hrm = need(user('hr.manager'), 'hr.manager')
    emp = need(env['hr.employee'].search([('name', '=', 'TEST Contract employee')], limit=1), 'TEST Contract employee')
    res = emp.with_user(hrm).action_new_departure()
    return f"opens {res.get('res_model')}"


check('P9', 'end of collaboration opens for TEST Contract employee', p9)


def p10():
    store = need(user('storekeeper'), 'storekeeper')
    lot = need(env['stock.lot'].search([('name', '=', 'TEST-VAX-EXPIRED')], limit=1), 'TEST-VAX-EXPIRED')
    wh = env['stock.warehouse'].search([], limit=1)
    quant = need(env['stock.quant'].search([('lot_id', '=', lot.id), ('location_id.usage', '=', 'internal'), ('quantity', '>', 0)], limit=1),
                 'expired stock')
    move = env['stock.move'].sudo().create({'product_id': lot.product_id.id, 'product_uom_qty': 1, 'uom_id': lot.product_id.uom_id.id,
                                            'location_id': quant.location_id.id, 'location_dest_id': wh.wh_output_stock_loc_id.id})
    move._action_confirm()
    move.move_line_ids.unlink()
    env['stock.move.line'].sudo().create({'move_id': move.id, 'product_id': lot.product_id.id, 'lot_id': lot.id, 'quantity': 1,
                                          'picked': True, 'location_id': quant.location_id.id,
                                          'location_dest_id': wh.wh_output_stock_loc_id.id})
    move.with_user(store)._action_done()
    return 'moved'


check('P10', 'expired batch cannot move to Output', p10, expect_error='UserError')


def p11():
    preg = need(env['alfia.pregnancy'].search([('mare_id.name', '=', 'TEST ET recipient mare'), ('state', 'in', ('bred', 'pregnant'))],
                                              limit=1), 'TEST ET recipient pregnancy')
    return preg.display_name


check('P11', 'TEST embryo-transfer recipient is offered', p11)


def r1():
    clerk = need(user('sales.clerk'), 'sales.clerk')
    env['stock.scrap.reason.tag'].with_user(clerk).search([], limit=1)     # the Return screen reads these
    picking = need(env['stock.picking'].with_user(clerk).search([('state', 'in', ('assigned', 'confirmed', 'draft'))], limit=1),
                   'an open transfer the clerk can see')
    try:
        picking.with_user(clerk).button_validate()
    except Exception as e:  # noqa: BLE001
        if 'warehouse staff' in str(e):
            raise
        raise ValueError(f"refused for another reason: {str(e).splitlines()[0][:120]}")
    return 'validated'


check('R1', 'sales.clerk can open Return, but validating a transfer is for the store', r1, expect_error='AccessError')


def r2():
    acc = need(user('accountant'), 'accountant')
    partner = need(env['res.partner'].search([('customer_rank', '>', 0)], limit=1) or env['res.partner'].search([], limit=1), 'a customer')
    credit = env['account.move'].with_user(acc).create({
        'move_type': 'out_refund', 'partner_id': partner.id, 'invoice_date': fields.Date.today(),
        'invoice_line_ids': [(0, 0, {'name': 'VERIFY credit', 'quantity': 1, 'price_unit': 10})]})
    credit.with_user(acc).action_post()
    return 'posted by its preparer'


check('R2', 'a credit note is not posted by the person who prepared it', r2, expect_error='UserError')

env.cr.rollback()
print(f"VERIFY SUMMARY {results.count('PASS')} passed, {results.count('FAIL')} failed, {results.count('SKIP')} skipped "
      f"(nothing saved; checked {fields.Datetime.now()} UTC)")
