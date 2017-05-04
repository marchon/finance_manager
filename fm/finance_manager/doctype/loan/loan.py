# -*- coding: utf-8 -*-
# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from math import ceil
import erpnext
from frappe import _
from frappe.utils import flt, rounded, add_months, nowdate
from erpnext.controllers.accounts_controller import AccountsController

PENDING = "PENDIENTE"

class Loan(AccountsController):
	def validate(self):
		check_repayment_method(self.repayment_method, self.loan_amount, self.monthly_repayment_amount, self.repayment_periods)
		if not self.company:
			self.company = erpnext.get_default_company()

		if not self.posting_date:
			self.posting_date = nowdate()

		if not self.repayment_method == "Repay Fixed Amount per Period":
			self.monthly_repayment_amount = flt(self.loan_amount) / flt(self.repayment_periods)

		else:
			self.set_repayment_period()

		if self.interest_type=="Simple":
			if not self.rate_of_interest: 	
				self.rate_of_interest = frappe.db.get_single_value("FM Configuration", "simple_rate_of_interest")

			self.make_simple_repayment_schedule()

		elif self.interest_type=="Composite":
			if not self.rate_of_interest: 
				self.rate_of_interest = frappe.db.get_single_value("FM Configuration", "composite_rate_of_interest")

			self.make_repayment_schedule()

		self.calculate_totals()

	def make_jv_entry(self):
		self.check_permission('write')
		journal_entry = frappe.new_doc('Journal Entry')
		journal_entry.voucher_type = 'Bank Entry'
		journal_entry.user_remark = _('Desembolso de Prestamo: {0}').format(self.name)
		journal_entry.company = self.company
		journal_entry.loan = self.name
		journal_entry.posting_date = nowdate()
		journal_entry.cheque_date = nowdate()

		account_amt_list = []

		account_amt_list.append({
			"account": self.customer_loan_account,
			"party_type": "Customer",
			"party": self.customer,
			"debit_in_account_currency": self.loan_amount,
			"reference_type": "Loan",
			"reference_name": self.name,
			})
		account_amt_list.append({
			"account": self.payment_account,
			"credit_in_account_currency": self.loan_amount,
			"reference_type": "Loan",
			"reference_name": self.name,
			})
		journal_entry.set("accounts", account_amt_list)
		return journal_entry.as_dict()

	def make_simple_repayment_schedule(self):
		self.repayment_schedule = []
		interest_rate_dec = flt(self.rate_of_interest) / 100
		payment_date  =  self.disbursement_date 
		balance_amount  =  self.loan_amount  
		fecha = self.disbursement_date
		interes = self.loan_amount * interest_rate_dec
		capital = self.monthly_repayment_amount
		balance_interes = self.loan_amount * interest_rate_dec * int(self.repayment_periods)
		balance_capital = self.loan_amount
		capital_acumulado = capital
		interes_acumulado = interes
		pagos_acumulados = self.monthly_repayment_amount
		payment_date = self.disbursement_date
		
		while(balance_capital > 0):
			balance_capital -=  ceil(capital)
			balance_interes -=  ceil(interes)

			if balance_capital < 0 or balance_interes < 0:
				balance_capital = balance_interes = 0

			self.append("repayment_schedule", {
				"fecha": payment_date,
				"capital":  ceil(capital),
				"interes":  ceil(interes),
				"balance_capital": balance_capital,
				"balance_interes": balance_interes,
				"capital_acumulado": ceil(capital_acumulado),
				"interes_acumulado": ceil(interes_acumulado),
				"pagos_acumulados": rounded(pagos_acumulados + interes),
				"estado": PENDING
			})

			payment_date = add_months(payment_date, 1)
			capital_acumulado += ceil(capital)
			interes_acumulado += ceil(interes)
			pagos_acumulados += (self.monthly_repayment_amount + interes)

		self.monthly_repayment_amount += interes

	def make_repayment_schedule(self):
		self.repayment_schedule = []
		payment_date = self.disbursement_date
		balance_amount = self.loan_amount

		while(balance_amount > 0):
			interest_amount = balance_amount * flt(self.rate_of_interest) / (12*100)
			principal_amount = self.monthly_repayment_amount - interest_amount
			balance_amount = balance_amount + interest_amount - self.monthly_repayment_amount

			if balance_amount < 0:
				principal_amount += balance_amount
				balance_amount = 0.0

			total_payment = principal_amount + interest_amount

			self.append("repayment_schedule", {
				"payment_date": payment_date,
				"principal_amount": principal_amount,
				"interest_amount": round(interest_amount),
				"total_payment": total_payment,
				"balance_loan_amount": round(balance_amount)
			})

			next_payment_date = add_months(payment_date, 1)
			payment_date = next_payment_date

	def set_repayment_period(self):
		if self.repayment_method == "Repay Fixed Amount per Period":
			repayment_periods = len(self.repayment_schedule)

			self.repayment_periods = repayment_periods

	def calculate_totals(self):
		last_index = len(self.repayment_schedule) - 1 # To fetch the last row
		last_row = self.repayment_schedule[last_index]

		self.total_payment = last_row.pagos_acumulados
		self.total_interest_payable = last_row.interes_acumulado


def update_disbursement_status(doc):
	disbursement = frappe.db.sql("""select posting_date, ifnull(sum(debit_in_account_currency), 0) as disbursed_amount 
		from `tabGL Entry` where against_voucher_type = 'Loan' and against_voucher = %s""", 
		(doc.name), as_dict=1)[0]
	if disbursement.disbursed_amount == doc.loan_amount:
		frappe.db.set_value("Loan", doc.name , "status", "Fully Disbursed")
	if disbursement.disbursed_amount < doc.loan_amount and disbursement.disbursed_amount != 0:
		frappe.db.set_value("Loan", doc.name , "status", "Partially Disbursed")
	if disbursement.disbursed_amount == 0:
		frappe.db.set_value("Loan", doc.name , "status", "Sanctioned")
	if disbursement.disbursed_amount > doc.loan_amount:
		frappe.throw(_("Disbursed Amount cannot be greater than Loan Amount {0}").format(doc.loan_amount))
	if disbursement.disbursed_amount > 0:
		frappe.db.set_value("Loan", doc.name , "disbursement_date", disbursement.posting_date)  
	
def check_repayment_method(repayment_method, loan_amount, monthly_repayment_amount, repayment_periods):
	if repayment_method == "Repay Over Number of Periods" and not repayment_periods:
		frappe.throw(_("Please enter Repayment Periods"))
		
	if repayment_method == "Repay Fixed Amount per Period":
		if not monthly_repayment_amount:
			frappe.throw(_("Please enter repayment Amount"))

		if monthly_repayment_amount > loan_amount:
			frappe.throw(_("Monthly Repayment Amount cannot be greater than Loan Amount"))

def get_monthly_repayment_amount(interest_type,repayment_method, loan_amount, rate_of_interest, repayment_periods):
	if interest_type == "Composite": 	
		if rate_of_interest:
			monthly_interest_rate = flt(rate_of_interest) / (12 *100)
			return ceil((loan_amount * monthly_interest_rate *
				(1 + monthly_interest_rate)**repayment_periods) \
				/ ((1 + monthly_interest_rate)**repayment_periods - 1))
	elif rate_of_interest == "Simple":
		return ceil(flt(loan_amount) / repayment_periods)

@frappe.whitelist()
def get_loan_application(loan_application):
	loan = frappe.get_doc("Loan Application", loan_application)
	if loan:
		return loan

@frappe.whitelist()
def make_jv_entry(customer_loan, company, customer_loan_account, customer, loan_amount, payment_account):
	journal_entry = frappe.new_doc('Journal Entry')
	journal_entry.voucher_type = 'Bank Entry'
	journal_entry.user_remark = _('Desembolso de Prestamo: {0}').format(customer_loan)
	journal_entry.company = company
	journal_entry.posting_date = nowdate()

	account_amt_list = []

	account_amt_list.append({
		"account": customer_loan_account,
		"debit_in_account_currency": loan_amount,
		"reference_type": "Loan",
		"reference_name": customer_loan,
		})
	account_amt_list.append({
		"account": payment_account,
		"credit_in_account_currency": loan_amount,
		"reference_type": "Loan",
		"reference_name": customer_loan,
		})
	journal_entry.set("accounts", account_amt_list)
	return journal_entry.as_dict()
