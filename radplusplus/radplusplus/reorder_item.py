# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
import erpnext
from frappe.utils import flt, nowdate, add_days, cint
from frappe import _

def reorder_item():
	""" Reorder item if stock reaches reorder level"""
	# if initial setup not completed, return
	if not (frappe.db.a_row_exists("Company") and frappe.db.a_row_exists("Fiscal Year")):
		frappe.msgprint(frappe._('erreur 1 '))
		return

	if cint(frappe.db.get_value('Stock Settings', None, 'auto_indent')):
		frappe.msgprint(frappe._('go 1 '))
		return _reorder_item()

def _reorder_item():
	frappe.msgprint(frappe._('go 2 '))
	material_requests = {"Purchase": {}, "Transfer": {}, "Material Issue": {}, "Manufacture": {}}
	warehouse_company = frappe._dict(frappe.db.sql("""select name, company from `tabWarehouse`
		where disabled=0"""))
	default_company = (erpnext.get_default_company() or
		frappe.db.sql("""select name from tabCompany limit 1""")[0][0])

	# liste des items pour commande direct.
	production_order_items_to_consider = frappe.db.sql_list("""select *, po.source_warehouse as warehouse,
		po.company as company, i.default_material_request_type as material_request_type,
		i.default_supplier as supplier
		from `tabWork Order Item` poi inner join `tabWork Order` po on poi.parent=po.name
		inner join `tabItem` i on poi.item_code=i.name
		where poi.direct=1 and po.docstatus=1 
		""")
	#frappe.msgprint(frappe._('production_order_items_to_consider ' + str(production_order_items_to_consider)))
	#frappe.msgprint(frappe._('production_order_items_to_consider[item_code] ' + str(production_order_items_to_consider[item_code])))
	# if not production_order_items_to_consider:
		# return
	
	
	
	def add_to_direct_material_request(item_code, warehouse, required_qty, transferred_qty, material_request_type,
		production_order_item_parent, production_order_item_name):
		if warehouse not in warehouse_company:
			# a disabled warehouse
			return

		company = warehouse_company.get(warehouse) or default_company

		material_requests[material_request_type].setdefault(company, []).append({
			"item_code": item_code,
			"warehouse": warehouse,
			"reorder_qty": required_qty,
			"production_order_item_parent":production_order_item_parent,
			"production_order_item_name":production_order_item_name
		})
		
	if production_order_items_to_consider :
		material_request_qty = get_material_request_qty(production_order_items_to_consider)
		frappe.msgprint(frappe._('material_request_qty ' + str(material_request_qty)))
	
		for poi in production_order_items_to_consider:
			production_order_item = frappe.get_doc("Production Order Item", poi)
			production_order = frappe.get_doc("Production Order", production_order_item.parent)
			item = frappe.get_doc("Item", production_order_item.item_code)
			if production_order_item.name not in material_request_qty:
				add_to_direct_material_request(item.item_code, production_order.source_warehouse, 
					production_order_item.required_qty,	production_order_item.transferred_qty,
					item.default_material_request_type, production_order_item.parent, production_order_item.name)
	
	if material_requests:
		frappe.msgprint(frappe._('material_requests ' + str(material_requests)))
		list_direct = create_direct_material_request(material_requests)
		material_requests = {"Purchase": {}, "Transfer": {}, "Material Issue": {}, "Manufacture": {}}
	
	items_to_consider = frappe.db.sql_list("""select name from `tabItem` item
		where is_stock_item=1 and has_variants=0
			and disabled=0
			and (end_of_life is null or end_of_life='0000-00-00' or end_of_life > %(today)s)
			and (exists (select name from `tabItem Reorder` ir where ir.parent=item.name)
				or (variant_of is not null and variant_of != ''
				and exists (select name from `tabItem Reorder` ir where ir.parent=item.variant_of))
			)""",
		{"today": nowdate()})

	if not items_to_consider and not production_order_items_to_consider:
		return

	item_warehouse_projected_qty = get_item_warehouse_projected_qty(items_to_consider)

	def add_to_material_request(item_code, warehouse, reorder_level, reorder_qty, material_request_type, warehouse_group=None):
		if warehouse not in warehouse_company:
			# a disabled warehouse
			return

		reorder_level = flt(reorder_level)
		reorder_qty = flt(reorder_qty)

		# projected_qty will be 0 if Bin does not exist
		if warehouse_group:
			projected_qty = flt(item_warehouse_projected_qty.get(item_code, {}).get(warehouse_group))
		else:
			projected_qty = flt(item_warehouse_projected_qty.get(item_code, {}).get(warehouse))

		if (reorder_level or reorder_qty) and projected_qty < reorder_level:
			deficiency = reorder_level - projected_qty
			if deficiency > reorder_qty:
				reorder_qty = deficiency

			company = warehouse_company.get(warehouse) or default_company

			material_requests[material_request_type].setdefault(company, []).append({
				"item_code": item_code,
				"warehouse": warehouse,
				"reorder_qty": reorder_qty
			})

	for item_code in items_to_consider:
		item = frappe.get_doc("Item", item_code)

		if item.variant_of and not item.get("reorder_levels"):
			item.update_template_tables()

		if item.get("reorder_levels"):
			for d in item.get("reorder_levels"):
				add_to_material_request(item_code, d.warehouse, d.warehouse_reorder_level,
					d.warehouse_reorder_qty, d.material_request_type, warehouse_group=d.warehouse_group)
	if material_requests:
		list_stock = create_material_request(material_requests)
	
	mr_list = list_direct + list_stock or []
	
	frappe.msgprint(frappe._('MRP finished.'))
	
	if mr_list:
		if getattr(frappe.local, "reorder_email_notify", None) is None:
			frappe.local.reorder_email_notify = cint(frappe.db.get_value('Stock Settings', None,
				'reorder_email_notify'))

		if(frappe.local.reorder_email_notify):
			send_email_notification(mr_list)

def get_material_request_qty(production_order_items_to_consider):
	material_request_qty = {}

	frappe.msgprint(frappe._('production_order_items_to_consider : ' + str(production_order_items_to_consider)))
	
	for production_order, production_order_item, item_code, warehouse, qty in frappe.db.sql("""select item_code, warehouse, production_order_item,
		production_order, qty
		from `tabMaterial Request Item`
		where production_order_item in ({0})
			and (warehouse != "" and warehouse is not null)"""\
		.format(", ".join(["%s"] * len(production_order_items_to_consider))), production_order_items_to_consider):
		
		#material_request_qty.setdefault(production_order, {})[warehouse][item_code] = flt(qty)
		material_request_qty.setdefault(item_code, {})[warehouse] = flt(qty)
				
	return material_request_qty
	
def get_item_warehouse_projected_qty(items_to_consider):
	item_warehouse_projected_qty = {}

	for item_code, warehouse, projected_qty in frappe.db.sql("""select item_code, warehouse, projected_qty
		from tabBin where item_code in ({0})
			and (warehouse != "" and warehouse is not null)"""\
		.format(", ".join(["%s"] * len(items_to_consider))), items_to_consider):
		
		item_warehouse_projected_qty.setdefault(item_code, {})[warehouse] = flt(projected_qty)
		
		warehouse_doc = frappe.get_doc("Warehouse", warehouse)
		
		if warehouse_doc.parent_warehouse:
			if not item_warehouse_projected_qty.get(item_code, {}).get(warehouse_doc.parent_warehouse):
				item_warehouse_projected_qty.setdefault(item_code, {})[warehouse_doc.parent_warehouse] = flt(projected_qty)
			else:
				item_warehouse_projected_qty[item_code][warehouse_doc.parent_warehouse] += flt(projected_qty)
				
	return item_warehouse_projected_qty

def create_direct_material_request(material_requests):
	"""	Create indent on reaching reorder level	"""
	mr_list = []
	exceptions_list = []
	
	#frappe.msgprint(frappe._('material_requests ' + str(material_requests)))
	
	def _log_exception():
		if frappe.local.message_log:
			exceptions_list.extend(frappe.local.message_log)
			frappe.local.message_log = []
		else:
			exceptions_list.append(frappe.get_traceback())
					
	for request_type in material_requests:
		for company in material_requests[request_type]:
			
			try:
				items = material_requests[request_type][company]
				if not items:
					continue
				
				#items = material_requests[request_type][company]

				mr = frappe.new_doc("Material Request")
				mr.update({
					"company": company,
					"transaction_date": nowdate(),
					"material_request_type": request_type
				})

				for d in items:
					d = frappe._dict(d)
					item = frappe.get_doc("Item", d.item_code)
					mr.append("items", {
						"doctype": "Material Request Item",
						"item_code": d.item_code,
						"schedule_date": add_days(nowdate(),cint(item.lead_time_days)),
						"uom":	item.stock_uom,
						"warehouse": d.warehouse,
						"item_name": item.item_name,
						"description": item.description,
						"item_group": item.item_group,
						"qty": d.reorder_qty,
						"brand": item.brand,
						"production_order":d.production_order_item_parent,
						"production_order_item":d.production_order_item_name,
					})

				mr.insert()
				mr.submit()
				mr_list.append(mr)

			except:
				_log_exception()

	if exceptions_list:
		notify_errors(exceptions_list)

	return mr_list
	
def create_material_request(material_requests):
	"""	Create indent on reaching reorder level	"""
	mr_list = []
	exceptions_list = []

	def _log_exception():
		if frappe.local.message_log:
			exceptions_list.extend(frappe.local.message_log)
			frappe.local.message_log = []
		else:
			exceptions_list.append(frappe.get_traceback())

	for request_type in material_requests:
		for company in material_requests[request_type]:
			try:
				items = material_requests[request_type][company]
				if not items:
					continue

				mr = frappe.new_doc("Material Request")
				mr.update({
					"company": company,
					"transaction_date": nowdate(),
					"material_request_type": "Material Transfer" if request_type=="Transfer" else request_type
				})

				for d in items:
					d = frappe._dict(d)
					item = frappe.get_doc("Item", d.item_code)
					mr.append("items", {
						"doctype": "Material Request Item",
						"item_code": d.item_code,
						"schedule_date": add_days(nowdate(),cint(item.lead_time_days)),
						"uom":	item.stock_uom,
						"warehouse": d.warehouse,
						"item_name": item.item_name,
						"description": item.description,
						"item_group": item.item_group,
						"qty": d.reorder_qty,
						"brand": item.brand,
						"production_order":"",
						"production_order_item":"",
					})

				mr.insert()
				mr.submit()
				mr_list.append(mr)

			except:
				_log_exception()

	if mr_list:
		if getattr(frappe.local, "reorder_email_notify", None) is None:
			frappe.local.reorder_email_notify = cint(frappe.db.get_value('Stock Settings', None,
				'reorder_email_notify'))

		if(frappe.local.reorder_email_notify):
			send_email_notification(mr_list)

	if exceptions_list:
		notify_errors(exceptions_list)

	return mr_list

def send_email_notification(mr_list):
	""" Notify user about auto creation of indent"""

	email_list = frappe.db.sql_list("""select distinct r.parent
		from tabUserRole r, tabUser p
		where p.name = r.parent and p.enabled = 1 and p.docstatus < 2
		and r.role in ('Purchase Manager','Stock Manager')
		and p.name not in ('Administrator', 'All', 'Guest')""")

	msg = frappe.render_template("templates/emails/reorder_item.html", {
		"mr_list": mr_list
	})

	frappe.sendmail(recipients=email_list,
		subject=_('Auto Material Requests Generated'), message = msg)

def notify_errors(exceptions_list):
	subject = "[Important] [ERPNext] Auto Reorder Errors"
	content = """Dear System Manager,

An error occured for certain Items while creating Material Requests based on Re-order level.

Please rectify these issues:
---
<pre>
%s
</pre>
---
Regards,
Administrator""" % ("\n\n".join(exceptions_list),)

	from frappe.email import sendmail_to_system_managers
	sendmail_to_system_managers(subject, content)
