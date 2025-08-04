import base64
import json

import frappe
import requests
from frappe.model.document import Document
from frappe.utils.password import get_decrypted_password
from jinja2 import Template


class AIService(Document):
	"""Controller for AI Service DocType"""

	def validate(self):
		"""
		Validate the document before saving.
		For API Key auth, ensure api_key_header and api_key_value are set.
		"""
		self.validate_unique_default()
		if getattr(self, "auth_type", None) == "API Key":
			if not getattr(self, "api_key_header", None) or not getattr(self, "api_key_value", None):
				frappe.throw(
					"For API Key authentication, you must set both the API Key Header and API Key Value."
				)

	def validate_unique_default(self):
		if self.is_default:
			existing_default = frappe.get_all(
				"AI Service",
				filters={"service_type": self.service_type, "is_default": 1, "name": ["!=", self.name]},
			)
			if existing_default:
				frappe.throw(
					f"Only one default service allowed per service type. "
					f"Please uncheck default for existing {self.service_type} service."
				)

	def on_update(self):
		frappe.clear_cache(doctype="AI Service")

	def on_trash(self):
		remaining_services = frappe.get_all(
			"AI Service",
			filters={"service_type": self.service_type, "is_active": 1, "name": ["!=", self.name]},
		)
		if not remaining_services:
			frappe.throw(
				f"Cannot delete the last active {self.service_type} service. "
				f"Please add another service first."
			)

	@frappe.whitelist()
	def ping(self):
		"""
		Generic ping: POST or GET to the configured base_url with user-provided headers and payload.
		"""
		hook_result = self._try_hooks()
		if hook_result is not None:
			return hook_result
		self._validate_ping_request()
		result = self._execute_ping_request()
		return result

	def _try_hooks(self):
		"""Try to get result from hooks first."""
		hook_paths = frappe.get_hooks("ai_service_ping")
		for path in hook_paths:
			hook_fn = frappe.get_attr(path)
			result = hook_fn(doc=self)
			if result is not None:
				return result
		return None

	def _validate_ping_request(self):
		"""Validate ping request requirements."""
		if not self.base_url:
			frappe.throw(frappe._("Base URL is required"))

	def _execute_ping_request(self):
		"""Execute the ping request."""
		headers = self._build_headers()
		payload = self._build_payload()
		http_method = self._get_http_method()
		return self._make_request(http_method, self.base_url, headers, payload, "ping")

	def _run_ping_hooks(self):
		hook_paths = frappe.get_hooks("ai_service_ping")
		for path in hook_paths:
			hook_fn = frappe.get_attr(path)
			result = hook_fn(doc=self)
			if result is not None:
				return result
		return None

	def _build_headers(self):
		"""
		Build headers for outgoing request.
		- Bearer: use parent api_key for Authorization header
		- API Key: use api_key_header and api_key_value (decrypted) for header
		- Basic: use parent username/password for Authorization header
		- None: no auth header from parent
		- Always build all other headers from custom_headers child table
		"""
		headers = self._init_headers()
		headers = self._add_auth_headers(headers)
		headers = self._add_custom_headers(headers)
		return headers

	def _init_headers(self):
		"""Initialize basic headers."""
		return {"Content-Type": "application/json"}

	def _add_auth_headers(self, headers):
		"""Add authentication headers based on auth type."""
		auth_type = getattr(self, "auth_type", "None")

		if auth_type == "Bearer":
			headers = self._add_bearer_auth(headers)
		elif auth_type == "API Key":
			headers = self._add_api_key_auth(headers)
		elif auth_type == "Basic":
			headers = self._add_basic_auth(headers)

		return headers

	def _add_bearer_auth(self, headers):
		"""Add Bearer token authentication to headers."""
		api_key = (
			get_decrypted_password(self.doctype, self.name, "api_key", raise_exception=False) or self.api_key
		)
		headers["Authorization"] = f"Bearer {api_key}"
		return headers

	def _add_api_key_auth(self, headers):
		"""Add API Key authentication to headers."""
		key = getattr(self, "api_key_header", "x-api-key")
		value = (
			get_decrypted_password(self.doctype, self.name, "api_key_value", raise_exception=False)
			or self.api_key_value
		)
		headers[key] = value
		return headers

	def _add_basic_auth(self, headers):
		"""Add Basic authentication to headers."""
		username = getattr(self, "username", None)
		password = (
			get_decrypted_password(self.doctype, self.name, "password", raise_exception=False)
			or self.password
		)
		if username and password:
			token = base64.b64encode(f"{username}:{password}".encode()).decode()
			headers["Authorization"] = f"Basic {token}"
		return headers

	def _add_custom_headers(self, headers):
		"""Add custom headers from child table."""
		if hasattr(self, "custom_headers") and self.custom_headers:
			for row in self.custom_headers:
				key = row.header_key
				value = row.header_value
				if getattr(row, "is_sensitive", 0):
					value = (
						get_decrypted_password(row.doctype, row.name, "header_value", raise_exception=False)
						or value
					)
				headers[key] = value
		return headers

	def _build_payload(self):
		payload = {}
		if getattr(self, "custom_payload", None):
			payload = self._parse_custom_payload()
		else:
			payload = self._build_default_payload()
		return payload

	def _parse_custom_payload(self):
		"""Parse custom payload from string or dict."""
		try:
			payload = self.custom_payload
			if isinstance(payload, str):
				payload = json.loads(payload)
			return payload
		except Exception as e:
			frappe.throw(f"Invalid custom_payload: {e!s}")

	def _build_default_payload(self):
		"""Build default payload for AI service."""
		if getattr(self, "model_name", None):
			payload = {
				"model": self.model_name,
				"max_tokens": 100,
				"messages": [
					{
						"role": "user",
						"content": "Ping test from Frappe. Please respond with 'pong' or similar.",
					}
				],
			}
			return payload
		return {}

	def _get_http_method(self):
		"""Get HTTP method with fallback to POST."""
		return getattr(self, "http_method", None) or "POST"

	def _get_timeout(self, request_type="default"):
		"""Get timeout based on request type."""
		timeouts = {"ping": 30, "test": 90, "ai_call": 90, "default": 60}
		return timeouts.get(request_type, timeouts["default"])

	def _make_request(self, http_method, url, headers, payload, request_type="default"):
		"""Generic method to make HTTP requests."""
		try:
			resp = self._send_request(http_method, url, headers, payload, request_type)
			return self._handle_response(resp, request_type)
		except Exception as e:
			if request_type == "ping":
				frappe.throw(f"Ping failed: {e!s}")
			else:
				raise e

	def _send_request(self, http_method, url, headers, payload, request_type="default"):
		"""Send HTTP request to the service."""
		timeout = self._get_timeout(request_type)

		if http_method.upper() == "POST":
			return requests.post(url, headers=headers, json=payload, timeout=timeout)
		else:
			return requests.get(url, headers=headers, timeout=timeout)

	def _handle_response(self, resp, request_type="default"):
		"""Handle the response from the service."""
		if resp.status_code < 400:
			return self._handle_success_response(resp, request_type)
		else:
			return self._handle_error_response(resp, request_type)

	def _handle_success_response(self, resp, request_type):
		"""Handle successful response based on request type."""
		handlers = {
			"ping": self._handle_ping_success,
			"test": self._handle_test_success,
			"ai_call": self._handle_ai_success,
		}

		handler = handlers.get(request_type, self._handle_generic_success)
		return handler(resp)

	def _handle_ping_success(self, resp):
		"""Handle successful ping response."""
		try:
			data = resp.json()
			msg = frappe._("Ping successful! Response: ") + str(data)
		except Exception:
			msg = frappe._("Ping successful! Status: ") + str(resp.status_code)
		frappe.msgprint(msg, title=frappe._("Ping Result"), indicator="green")
		return msg

	def _handle_test_success(self, resp):
		"""Handle successful test response."""
		service_name = self.service_type or "AI Service"
		msg = frappe._(f"Connection established successfully with {service_name}")
		frappe.msgprint(msg, title=frappe._("Connection Test"), indicator="green")
		return {"status": "success", "message": msg}

	def _handle_ai_success(self, resp):
		"""Handle successful AI response."""
		try:
			data = resp.json()
			return {"status": "success", "data": data, "raw_response": resp.text}
		except Exception:
			return {"status": "success", "data": {"content": resp.text}, "raw_response": resp.text}

	def _handle_generic_success(self, resp):
		"""Handle generic successful response."""
		return {"status": "success", "data": resp.text}

	def _handle_error_response(self, resp, request_type):
		"""Handle error response based on request type."""
		error_handlers = {
			"ping": lambda r: frappe.throw(f"Ping failed. Status: {r.status_code}, Response: {r.text}"),
			"test": self._handle_test_error,
			"ai_call": lambda r: frappe.throw(
				frappe._(f"AI call failed. Status: {r.status_code}, Response: {r.text}")
			),
		}

		handler = error_handlers.get(
			request_type,
			lambda r: frappe.throw(f"Request failed. Status: {r.status_code}, Response: {r.text}"),
		)
		return handler(resp)

	def _handle_test_error(self, resp):
		"""Handle test error response."""
		error_msg = self._get_error_message(resp.status_code)
		frappe.msgprint(error_msg, title=frappe._("Connection Test"), indicator="red")
		return {"status": "error", "message": error_msg}

	def _get_error_message(self, status_code):
		"""Get user-friendly error message based on status code."""
		error_messages = {
			401: frappe._("Authentication failed. Please check your credentials."),
			403: frappe._("Access denied. Please check your API permissions."),
			404: frappe._("Service not found. Please check the URL."),
		}

		if status_code in error_messages:
			return error_messages[status_code]
		elif status_code >= 500:
			return frappe._("Server error. Please try again later.")
		else:
			return frappe._(f"Connection failed with status code: {status_code}")

	def _handle_exception(self, e, request_type="default"):
		"""Handle exceptions based on request type."""
		if request_type == "test":
			return self._handle_test_exception(e)
		else:
			frappe.log_error(f"[AIService] {request_type} Exception: {e!s}")
			frappe.throw(frappe._(f"{request_type.title()} failed: {e!s}"))

	def _handle_test_exception(self, e):
		"""Handle test exceptions."""
		exception_handlers = {
			requests.exceptions.Timeout: self._handle_timeout_error,
			requests.exceptions.ConnectionError: self._handle_connection_error,
		}

		handler = exception_handlers.get(type(e), self._handle_generic_error)
		return handler(e)

	def _handle_timeout_error(self):
		"""Handle timeout error."""
		error_msg = frappe._("Connection timeout. Please check your network or try again.")
		frappe.msgprint(error_msg, title=frappe._("Connection Test"), indicator="red")
		return {"status": "error", "message": error_msg}

	def _handle_connection_error(self):
		"""Handle connection error."""
		error_msg = frappe._("Connection failed. Please check the URL and network connectivity.")
		frappe.msgprint(error_msg, title=frappe._("Connection Test"), indicator="red")
		return {"status": "error", "message": error_msg}

	def _handle_generic_error(self, e):
		"""Handle generic error."""
		error_msg = frappe._("Connection test failed: {e!s}")
		frappe.msgprint(error_msg, title=frappe._("Connection Test"), indicator="red")
		return {"status": "error", "message": error_msg}

	@frappe.whitelist()
	def test_connection(self):
		"""
		Test connection for this AI Service instance. Shows user-friendly message instead of raw response.
		"""
		headers = self._build_headers()
		url = self.base_url + getattr(self, "base_url_suffix", "")
		http_method = self._get_http_method()
		payload = self._build_test_payload()

		try:
			resp = self._send_request(http_method, url, headers, payload, "test")
			return self._handle_response(resp, "test")
		except Exception as e:
			return self._handle_exception(e, "test")

	def _build_test_payload(self):
		"""Build payload for connection test."""
		payload = json.loads(frappe.get_value("AI Service", self.name, "test_payload"))
		if payload:
			return payload
		else:
			return self._build_default_payload()

	@frappe.whitelist()
	def make_ai_call(
		self, user_prompt=None, system_prompt=None, custom_data=None, messages=None, payload=None
	):
		"""
		Make a call to the AI service with custom prompts and data.

		Args:
			user_prompt (str): Custom user prompt (overrides default)
			system_prompt (str): Custom system prompt (overrides default)
			custom_data (dict): Additional data to include in the prompt

		Returns:
			dict: Response from AI service
		"""

		if not self.is_active:
			frappe.throw(f"AI Service {self.name} is not active")

		request_data = self._prepare_ai_request(user_prompt, system_prompt, custom_data, messages, payload)
		return self._execute_ai_request(request_data)

	def _prepare_ai_request(self, user_prompt, system_prompt, custom_data, messages, payload):
		"""Prepare request data for AI call."""
		headers = self._build_headers()
		final_payload = self._build_ai_call_payload(
			user_prompt, system_prompt, custom_data, messages, payload
		)
		url = self.base_url + (getattr(self, "base_url_suffix", "") or "")
		http_method = self._get_http_method()

		return {"headers": headers, "payload": final_payload, "url": url, "http_method": http_method}

	def _execute_ai_request(self, request_data):
		"""Execute the AI request."""
		try:
			resp = self._send_request(
				request_data["http_method"],
				request_data["url"],
				request_data["headers"],
				request_data["payload"],
				"ai_call",
			)
			return self._handle_response(resp, "ai_call")
		except Exception as e:
			return self._handle_exception(e, "ai_call")

	def _build_ai_call_payload(
		self, user_prompt=None, system_prompt=None, custom_data=None, messages=None, payload=None
	):
		"""Build payload for AI service call with custom prompts."""
		final_system_prompt = self._get_final_system_prompt(system_prompt, custom_data)
		final_user_prompt = self._get_final_user_prompt(user_prompt, custom_data)

		messages = self._build_messages_array(messages, final_system_prompt, final_user_prompt)
		payload = self._build_final_payload(payload, messages, final_system_prompt)

		return payload

	def _get_final_system_prompt(self, system_prompt, custom_data):
		"""Get final system prompt with template rendering."""
		final_system_prompt = system_prompt or getattr(self, "system_prompt", "")
		if custom_data and final_system_prompt:
			final_system_prompt = self._render_jinja_template(final_system_prompt, custom_data)
		return final_system_prompt

	def _get_final_user_prompt(self, user_prompt, custom_data):
		"""Get final user prompt with template rendering."""
		final_user_prompt = user_prompt or getattr(self, "user_prompt", "")
		if custom_data and final_user_prompt:
			final_user_prompt = self._render_jinja_template(final_user_prompt, custom_data)
		return final_user_prompt

	def _build_messages_array(self, messages, final_system_prompt, final_user_prompt):
		"""Build messages array with system and user prompts."""
		if not messages:
			messages = []

		if final_system_prompt:
			messages = self._update_system_message(messages, final_system_prompt)

		if final_user_prompt:
			messages.append({"role": "user", "content": final_user_prompt})

		return messages

	def _update_system_message(self, messages, final_system_prompt):
		"""Update existing system message or add new one."""
		for message in messages:
			if message.get("role") == "system":
				message["content"] = final_system_prompt
				break
		return messages

	def _build_final_payload(self, payload, messages, final_system_prompt):
		"""Build final payload with messages and model."""
		if not payload:
			payload = {"messages": messages}
		else:
			payload["messages"] = messages

		if getattr(self, "model_name", None):
			payload["model"] = self.model_name

		if "system" in payload.keys() and final_system_prompt:
			payload["system"] = final_system_prompt

		return payload

	def _render_jinja_template(self, template, data):
		"""Render Jinja template with provided data."""
		try:
			template_obj = Template(template)
			return template_obj.render(**data)
		except Exception:
			return template


@frappe.whitelist()
def ping(docname):
	doc = frappe.get_doc("AI Service", docname)
	return doc.ping()


@frappe.whitelist()
def test_connection(docname):
	doc = frappe.get_doc("AI Service", docname)
	return doc.test_connection()


@frappe.whitelist()
def make_ai_call(
	docname, user_prompt=None, system_prompt=None, custom_data=None, messages=None, payload=None
):
	"""Wrapper function to make AI service call."""
	doc = frappe.get_doc("AI Service", docname)
	return doc.make_ai_call(user_prompt, system_prompt, custom_data, messages, payload)
