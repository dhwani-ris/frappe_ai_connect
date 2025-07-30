frappe.ui.form.on("AI Service", {
	refresh: function (frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(
				__("Test Connection"),
				function () {
					frappe.call({
						method: "frappe_ai_connect.ai_connect.doctype.ai_service.ai_service.test_connection",
						args: { docname: frm.doc.name },
						callback: function (r) {
							if (r.message) {
								let msg = "";
								if (r.message.status && r.message.status !== "error") {
									msg =
										__("Status: {0}", [r.message.status]) +
										"<br>" +
										__("Response:") +
										"<br><pre>" +
										JSON.stringify(r.message.response, null, 2) +
										"</pre>";
								} else {
									msg = __("Error: {0}", [
										r.message.error || r.message.response,
									]);
								}
								frappe.msgprint({
									title: __("Test Connection Result"),
									message: msg,
									indicator:
										r.message.status &&
										r.message.status !== "error" &&
										r.message.status < 400
											? "green"
											: "red",
								});
							}
						},
						error: function (err) {
							frappe.msgprint({
								title: __("Test Connection Failed"),
								message: err.message || __("Test failed"),
								indicator: "red",
							});
						},
					});
				},
				__("Testing")
			);
		}
	},
	// auth_type: function(frm) {
	//     frm.events.refresh(frm);
	// }
});
