app_name = "trading_control"
app_title = "Trading Control"
app_publisher = "Waheed Ali"
app_description = "Trading Control portal for MT5 EAs"
app_email = "admin@aiopctrading.com"
app_license = "mit"

required_apps = ["frappe"]

add_to_apps_screen = [
	{
		"name": "trading_control",
		"title": "Trading Control",
		"route": "/app/trading-control",
		"has_permission": "trading_control.permissions.has_app_permission",
	}
]

# Desk assets (built by esbuild from public/js/*.bundle.js)
app_include_js = "trading_control.bundle.js"

# Fixtures: roles, workflow and its states/actions. No secrets.
fixtures = [
	{"dt": "Role", "filters": [["name", "in", ["TC Viewer", "TC Admin", "TC Approver", "TC Integration", "Trading Assistant"]]]},
	{"dt": "Workflow State", "filters": [["name", "in", ["Draft", "Pending Approval", "Approved", "Pushed", "Applied", "Rejected", "Superseded", "Push Failed"]]]},
	{"dt": "Workflow Action Master", "filters": [["name", "in", ["Submit for Approval", "Approve", "Reject", "Send Back"]]]},
	{"dt": "Workflow", "filters": [["name", "in", ["EA Config Version Approval"]]]},
	{"dt": "Number Card", "filters": [["module", "=", "Trading Control"]]},
]


# Raven (soft dependency): decision note back to the source thread
doc_events = {
	"Config Change Request": {"on_update": "trading_control.raven_router.notify_decision"},
	"Trade Signal": {"on_update": "trading_control.raven_router.notify_decision"},
}

# Navbar shortcut to Raven chat (the /raven route exists only when Raven is installed)
standard_navbar_items = [
	{"item_label": "Chat (Raven)", "item_type": "Route", "route": "/raven", "is_standard": 1},
]

# Hamza Message: users see only their own conversation
permission_query_conditions = {
	"Hamza Message": "trading_control.permissions.hamza_message_query",
}
has_permission = {
	"Hamza Message": "trading_control.permissions.hamza_message_has_permission",
}

scheduler_events = {
	"cron": {
		# every minute: retry due pushes/webhooks, expire signals, mark stale EAs
		"* * * * *": [
			"trading_control.push.retry_due_pushes",
			"trading_control.hamza.retry_due_messages",
			"trading_control.tasks.expire_trade_signals",
			"trading_control.tasks.mark_stale_instances",
			"trading_control.raven_router.release_queues",
		],
	},
	"daily": [
		"trading_control.tasks.prune_status_history",
	],
}

after_install = "trading_control.install.after_install"
