import base64
from base64 import b64decode

from pypdf import PdfReader, PdfWriter

from odoo import fields, models
from odoo.exceptions import UserError, ValidationError


class PayrollManagamentWizard(models.TransientModel):
    _name = "payroll.management.wizard"
    _description = "Payroll Management"

    subject = fields.Char(
        help="Enter the title of the payroll whether it is the month, week, day, etc.",
        required=True,
    )
    payrolls = fields.Many2many(
        "ir.attachment",
        "payrol_rel",
        "doc_id",
        "attach_id3",
        copy=False,
        required=True,
    )

    def _employee_identifier_field(self):
        Employee = self.env["hr.employee"]
        if "l10n_ua_rnokpp" in Employee._fields:
            return "l10n_ua_rnokpp"
        return "identification_id"

    def _employee_identifier(self, employee):
        return employee[self._employee_identifier_field()]

    def send_payrolls(self):
        not_found = set()
        self.merge_pdfs()
        reader = PdfReader("/tmp/merged-pdf.pdf")
        employees = set()

        if not self.env.company.country_id:
            raise UserError(self.env._("You must to filled country field of company"))

        identifier_field = self._employee_identifier_field()

        for page in reader.pages:
            for value in page.extract_text().split():
                if self.validate_id(value) and value != self.env.company.vat:
                    employee = self.env["hr.employee"].search(
                        [(identifier_field, "=", value)],
                        limit=1,
                    )
                    if employee:
                        employees.add(employee)
                    else:
                        not_found.add(value)

        for employee in list(employees):
            identifier = self._employee_identifier(employee)
            if not identifier:
                continue

            pdfWriter = PdfWriter()
            for page in reader.pages:
                if identifier in page.extract_text():
                    pdfWriter.add_page(page)

            path = "/tmp/" + self.env._("Payroll ") + employee.name + ".pdf"

            if not employee.no_payroll_encryption:
                pdfWriter.encrypt(identifier, algorithm="AES-256")

            with open(path, "wb") as f:
                pdfWriter.write(f)

            self.send_mail(employee, path, identifier)

        action = self.env["ir.actions.actions"]._for_xml_id(
            "hr_payroll_document.payrolls_view_action"
        )
        action["views"] = [
            [self.env.ref("hr_payroll_document.view_payroll_tree").id, "list"]
        ]

        if not_found:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": self.env._("Employees not found"),
                    "message": self.env._("IDs whose employee has not been found: ")
                    + ", ".join(list(not_found)),
                    "sticky": True,
                    "type": "warning",
                    "next": action,
                },
            }

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": self.env._("Payrolls sent"),
                "message": self.env._("Payrolls sent to employees correctly"),
                "sticky": False,
                "type": "success",
                "next": action,
            },
        }

    def merge_pdfs(self):
        pdfs = []
        for file in self.payrolls:
            btes = b64decode(file.datas, validate=True)
            if btes[0:4] != b"%PDF":
                raise ValidationError(self.env._("Missing pdf file signature"))
            name = "/tmp/" + file.name
            with open(name, "wb") as f:
                f.write(btes)
            pdfs.append(name)

        merger = PdfWriter()
        for pdf in pdfs:
            merger.append(pdf)
        merger.write("/tmp/merged-pdf.pdf")
        merger.close()

    def send_mail(self, employee, path, identifier):
        with open(path, "rb") as pdf_file:
            encoded_string = base64.b64encode(pdf_file.read())

        ir_values = {
            "name": self.env._("Payroll")
            + "_"
            + self.subject
            + "_"
            + employee.name
            + ".pdf",
            "type": "binary",
            "datas": encoded_string,
            "store_fname": encoded_string,
            "res_model": "hr.employee",
            "res_id": employee.id,
            "document_type": "payroll",
        }

        self.env["ir.attachment.payroll.custom"].create(
            {
                "attachment_id": self.env["ir.attachment"].create(ir_values).id,
                "employee": employee.name,
                "subject": self.subject,
                "identification_id": identifier,
            }
        )

        mail_template = self.env.ref(
            "hr_payroll_document.payroll_employee_email_template"
        )
        data_id = [(6, 0, [self.env["ir.attachment"].create(ir_values).id])]
        mail_template.attachment_ids = data_id
        mail_template.with_context(subject=self.subject).send_mail(
            employee.id,
            force_send=True,
        )

    def validate_id(self, number):
        return self.env["res.partner"].simple_vat_check(
            self.env.company.country_id.code,
            number,
        )
