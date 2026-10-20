// Ask Hamza: one-to-one chat with the Hamza trading assistant.
// Times are shown in Qatar time (Asia/Qatar, UTC+3).
frappe.pages["ask-hamza"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Ask Hamza"),
		single_column: true,
	});
	wrapper.ask_hamza = new AskHamza(page, wrapper);
};

frappe.pages["ask-hamza"].on_page_show = function (wrapper) {
	wrapper.ask_hamza && wrapper.ask_hamza.set_reference_from_route();
};

class AskHamza {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.reference = null;
		this.seen = new Set();
		this.make();
		this.set_reference_from_route();
		this.load();
		frappe.realtime.on("hamza_message", (msg) => this.add(msg, true));
	}

	make() {
		this.$body = $(`
			<div class="ask-hamza" style="max-width: 860px; margin: 0 auto;">
				<div class="text-muted small mb-2">${__("Times shown in Qatar time (Asia/Qatar, UTC+3). Hamza can discuss and propose; only Waheed approves actions.")}</div>
				<div class="ah-ref mb-2"></div>
				<div class="ah-log border rounded p-3 mb-3" style="height: 60vh; overflow-y: auto; background: var(--bg-color);"></div>
				<div class="d-flex" style="gap: 8px;">
					<textarea class="form-control ah-input" rows="2" placeholder="${__("Message Hamza... (Ctrl+Enter to send)")}"></textarea>
					<button class="btn btn-primary ah-send">${__("Send")}</button>
				</div>
			</div>`).appendTo(this.page.body);
		this.$log = this.$body.find(".ah-log");
		this.$input = this.$body.find(".ah-input");
		this.$body.find(".ah-send").on("click", () => this.send());
		this.$input.on("keydown", (e) => {
			if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) this.send();
		});
		this.page.set_secondary_action(__("Refresh"), () => this.load());
	}

	set_reference_from_route() {
		const opts = frappe.route_options || {};
		if (opts.reference_doctype && opts.reference_name) {
			this.reference = { doctype: opts.reference_doctype, name: opts.reference_name };
			frappe.route_options = null;
		}
		this.render_reference();
	}

	render_reference() {
		const $ref = this.$body.find(".ah-ref").empty();
		if (!this.reference) return;
		const { doctype, name } = this.reference;
		$ref.html(`<span class="indicator-pill blue">${__("About")}: ${frappe.utils.escape_html(doctype)}
			<a href="/app/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}">${frappe.utils.escape_html(name)}</a></span>
			<a class="small ml-2 ah-clear" href="#">${__("clear")}</a>`);
		$ref.find(".ah-clear").on("click", (e) => {
			e.preventDefault();
			this.reference = null;
			this.render_reference();
		});
	}

	load() {
		frappe.call("trading_control.api.get_conversation", { limit: 200 }).then((r) => {
			this.$log.empty();
			this.seen.clear();
			(r.message || []).forEach((m) => this.add(m, false));
			this.scroll();
		});
	}

	format_time(ts) {
		if (!ts) return "";
		return frappe.datetime.str_to_user(ts) + " " + __("Qatar");
	}

	add(m, scroll) {
		if (!m || !m.name) return;
		const existing = this.$log.find(`[data-name="${m.name}"]`);
		const mine = m.direction === "To Hamza";
		const status = mine && m.status ? `<span class="text-muted small"> · ${__(m.status)}</span>` : "";
		const ref = m.reference_doctype && m.reference_name
			? `<div class="small text-muted">${frappe.utils.escape_html(m.reference_doctype)}: ${frappe.utils.escape_html(m.reference_name)}</div>`
			: "";
		const html = `
			<div class="mb-3 d-flex ${mine ? "justify-content-end" : "justify-content-start"}" data-name="${m.name}">
				<div class="p-2 rounded" style="max-width: 75%; background: ${mine ? "var(--blue-100)" : "var(--gray-100)"};">
					<div class="small font-weight-bold">${mine ? __("You") : "Hamza"}
						<span class="text-muted font-weight-normal">${this.format_time(m.creation)}</span>${status}</div>
					${ref}
					<div style="white-space: pre-wrap;">${frappe.utils.escape_html(m.body || "")}</div>
				</div>
			</div>`;
		if (existing.length) existing.replaceWith(html);
		else this.$log.append(html);
		this.seen.add(m.name);
		if (scroll) this.scroll();
	}

	scroll() {
		this.$log.scrollTop(this.$log[0].scrollHeight);
	}

	send() {
		const body = (this.$input.val() || "").trim();
		if (!body) return;
		this.$input.prop("disabled", true);
		frappe.call({
			method: "trading_control.api.send_hamza_message",
			args: {
				body,
				reference_doctype: this.reference && this.reference.doctype,
				reference_name: this.reference && this.reference.name,
			},
			callback: (r) => {
				this.$input.val("");
				r.message && this.add(r.message, true);
			},
			always: () => this.$input.prop("disabled", false).focus(),
		});
	}
}
