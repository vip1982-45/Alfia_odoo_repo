# Alfia HR

Odoo Community's HR apps, set up for the stud and held to the same controls as the rest of the system.

| Area | What it gives |
|---|---|
| Employees | Departments per stable and function, stud job positions (groom, head groom, vet, rider, farrier...), Qatar ID, passport and health-card expiry dates with reminders to HR 30 days ahead (visa and work-permit reminders come from Odoo). |
| Time off | Qatar Labour Law leave types: annual (21/28 days, line manager then HR), sick (with certificate), Hajj (unpaid, up to 20 days), maternity (50 days), compassionate, unpaid. National Day and Sport Day are loaded; add the two Eids each year. Every employee can request their own time off. |
| Onboarding | A new employee starts the "Stud onboarding" plan at once: documents, access request to the security officer, safety and horse-handling induction, uniform and kit, policies, probation review. |
| Offboarding | When a departure is applied (Odoo's departure, or archiving), the person's access profiles are removed and the account is switched off on that day, recorded in the security audit trail, and the "Stud offboarding" plan starts: collect assets, final settlement and end-of-service gratuity, exit interview, visa. |
| Reimbursements | Expense categories (fuel, travel, per diem, vet supplies, horse transport, phone); the line manager approves; payment needs two people (alfia_security). Expenses can be allocated to a horse's cost centre. |
| Attendance, recruitment, skills, presence, timesheets | Odoo's apps, installed. Fleet and equipment are linked to employees. |

Controls:
- Nobody approves their own time off, allocation or expense (Odoo would auto-approve an expense whose approver is the employee).
- HR staff see only the departments in their access profile; managers see their direct reports; everyone sees their own.
  The "HR manager" profile sees every department. "Add a site" also creates the site's department.
- The accounting entry behind an approved expense is made by the system, so approvers need no accounting access.

Check the leave figures against your contracts and HR adviser; payroll (and the Wage Protection System) is not part of
Odoo Community.
