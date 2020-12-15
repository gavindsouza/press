from datetime import datetime, timedelta

import frappe


def remove_baggage():
	"""Remove any remote files attached to the Site doc if older than 12 hours"""
	half_day = datetime.now() - timedelta(hours=12)
	or_filters = [
		["database_file", "!=", ""],
		["public_file", "!=", ""],
		["private_file", "!=", ""],
		["remote_config_file", "!=", ""],
		["remote_database_file", "!=", ""],
		["remote_public_file", "!=", ""],
		["remote_private_file", "!=", ""],
	]
	filters = [
		["creation", "<", half_day],
		["status", "not in", "Pending,Installing,Updating,Active,Broken"],
	]
	fields = [
		"remote_config_file",
		"remote_database_file",
		"remote_public_file",
		"remote_private_file",
	]

	sites = frappe.get_all(
		"Site", fields=["name"] + fields, filters=filters, or_filters=or_filters
	)

	for site in sites:
		# remove remote files attached to site
		remote_files = {x: site.get(x) for x in fields}

		for remote_file_type, remote_file_name in remote_files.items():
			# s3 uploads.frappe.cloud has a 1 day expiry rule for all objects, so we'll unset those files here
			frappe.db.set_value("Site", site.name, remote_file_type, None, for_update=False)


def expire_offsite_backups(site, offsite_expiry):
	remote_files_to_delete = []

	expired_offsite_backups = frappe.get_all(
		"Site Backup",
		filters={
			"site": site,
			"status": "Success",
			"files_availability": "Available",
			"offsite": True,
		},
		order_by="creation desc",
	)[offsite_expiry:]

	for offsite_backup in expired_offsite_backups:
		remote_files = frappe.db.get_value(
			"Site Backup",
			offsite_backup["name"],
			["remote_database_file", "remote_private_file", "remote_public_file"],
		)
		remote_files_to_delete.extend(remote_files)
		frappe.db.set_value(
			"Site Backup", offsite_backup["name"], "files_availability", "Unavailable"
		)

	return remote_files_to_delete


def expire_local_backups(site, local_expiry):
	expired_local_backups = frappe.get_all(
		"Site Backup",
		filters={
			"site": site,
			"status": "Success",
			"files_availability": "Available",
			"offsite": False,
			"creation": ("<", datetime.now() - timedelta(hours=local_expiry)),
		},
		pluck="name",
	)

	for local_backup in expired_local_backups:
		# we're assuming each Frappe site does it's work as per conf and marking them
		# as available
		frappe.db.set_value("Site Backup", local_backup, "files_availability", "Unavailable")


def cleanup_backups():
	"""Delete expired offsite backups and set statuses for old local ones"""
	import functools
	from press.press.doctype.remote_file.remote_file import delete_remote_backup_objects

	expired_remote_files = []
	sites = frappe.get_all(
		"Site", filters={"status": ("!=", "Archived")}, fields=["name", "bench"]
	)
	offsite_keep_count = (
		frappe.db.get_single_value("Press Settings", "offsite_backups_count") or 30
	)

	@functools.lru_cache(maxsize=128)
	def keep_backups_for_(bench):
		return (
			frappe.parse_json(
				frappe.db.get_value("Bench", bench, "config") or "{}"
			).keep_backups_for_hours
			or 24
		)

	for site in sites:
		expire_local_backups(
			site=site.name, local_expiry=keep_backups_for_(site.bench),
		)
		expired_sites_remote_files = expire_offsite_backups(
			site=site.name, offsite_expiry=offsite_keep_count,
		)
		expired_remote_files.extend(expired_sites_remote_files)

	delete_remote_backup_objects(expired_remote_files)

	frappe.db.commit()


def remove_logs():
	for doctype in (
		"Site Uptime Log",
		"Site Request Log",
		"Site Job Log",
	):
		frappe.db.delete(doctype, {"modified": ("<", datetime.now() - timedelta(days=10))})
		frappe.db.commit()

	frappe.db.delete(doctype, {"modified": ("<", datetime.now() - timedelta(days=1))})
	frappe.db.commit()
