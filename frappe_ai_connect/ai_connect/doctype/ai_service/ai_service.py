import base64
import json

import frappe
import requests
from frappe.model.document import Document
from frappe.utils.password import get_decrypted_password


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
		print(
			f"[AIService] validate_unique_default() for: {self.name}, is_default={self.is_default}, "
			f"service_type={self.service_type}"
		)
		if self.is_default:
			existing_default = frappe.get_all(
				"AI Service",
				filters={"service_type": self.service_type, "is_default": 1, "name": ["!=", self.name]},
			)
			print(f"[AIService] Found existing_default: {existing_default}")
			if existing_default:
				print("[AIService] Only one default service allowed per type. Throwing error.")
				frappe.throw(
					f"Only one default service allowed per service type. "
					f"Please uncheck default for existing {self.service_type} service."
				)
		print(f"[AIService] validate_unique_default() completed for: {self.name}")

	def on_update(self):
		print(f"[AIService] on_update() called for: {self.name}")
		frappe.clear_cache(doctype="AI Service")
		print(f"[AIService] on_update() completed for: {self.name}")

	def on_trash(self):
		print(f"[AIService] on_trash() called for: {self.name}")
		remaining_services = frappe.get_all(
			"AI Service",
			filters={"service_type": self.service_type, "is_active": 1, "name": ["!=", self.name]},
		)
		print(f"[AIService] Remaining services after trash: {remaining_services}")
		if not remaining_services:
			print("[AIService] Cannot delete last active service. Throwing error.")
			frappe.throw(
				f"Cannot delete the last active {self.service_type} service. "
				f"Please add another service first."
			)
		print(f"[AIService] on_trash() completed for: {self.name}")

	@frappe.whitelist()
	def ping(self):
		"""
		Generic ping: POST or GET to the configured base_url with user-provided headers and payload.
		"""
		print(f"[AIService] ping() called for: {self.name}")
		hook_result = self._try_hooks()
		if hook_result is not None:
			return hook_result
		self._validate_ping_request()
		result = self._execute_ping_request()
		print(f"[AIService] ping() completed for: {self.name}")
		return result

	def _try_hooks(self):
		"""Try to get result from hooks first."""
		hook_result = self._run_ping_hooks()
		if hook_result is not None:
			print(f"[AIService] ping() hook result: {hook_result}")
			return hook_result
		return None

	def _validate_ping_request(self):
		"""Validate ping request requirements."""
		if not self.base_url:
			print("[AIService] ping() Base URL is missing!")
			frappe.throw(frappe._("Base URL is required"))

	def _execute_ping_request(self):
		"""Execute the ping request."""
		headers = self._build_headers()
		payload = self._build_payload()
		http_method = getattr(self, "http_method", None) or "POST"
		print(f"[AIService] ping() HTTP method: {http_method}")
		return self._make_ping_request(http_method, headers, payload)

	def _run_ping_hooks(self):
		print(f"[AIService] _run_ping_hooks() called for: {self.name}")
		hook_paths = frappe.get_hooks("ai_service_ping")
		print(f"[AIService] _run_ping_hooks() found hooks: {hook_paths}")
		for path in hook_paths:
			print(f"[AIService] _run_ping_hooks() running: {path}")
			hook_fn = frappe.get_attr(path)
			result = hook_fn(doc=self)
			print(f"[AIService] _run_ping_hooks() result from {path}: {result}")
			if result is not None:
				print(f"[AIService] _run_ping_hooks() using result from {path}")
				return result
		print("[AIService] _run_ping_hooks() no hook returned a result.")
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
		print(f"[AIService] _build_headers() called for: {self.name}")
		headers = self._init_headers()
		headers = self._add_auth_headers(headers)
		headers = self._add_custom_headers(headers)
		print(f"[AIService] _build_headers() Final headers: {headers}")
		print(f"[AIService] _build_headers() completed for: {self.name}")
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
		masked_key = api_key[:4] + "..." + api_key[-4:] if api_key and len(api_key) > 8 else "****"
		print(f"[AIService] _build_headers() Using Bearer token: {masked_key}")
		headers["Authorization"] = f"Bearer {api_key}"
		return headers

	def _add_api_key_auth(self, headers):
		"""Add API Key authentication to headers."""
		key = getattr(self, "api_key_header", "x-api-key")
		value = (
			get_decrypted_password(self.doctype, self.name, "api_key_value", raise_exception=False)
			or self.api_key_value
		)
		masked_value = value[:4] + "..." + value[-4:] if value and len(value) > 8 else "****"
		print(f"[AIService] _build_headers() Using API Key header: {key}, value: {masked_value}")
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
			print(f"[AIService] _build_headers() Using Basic Auth for user: {username}")
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
					print(f"[AIService] _build_headers() Adding header: {key}: ***MASKED*** (sensitive)")
				else:
					print(f"[AIService] _build_headers() Adding header: {key}: {value}")
				headers[key] = value
		return headers

	def _build_payload(self):
		print(f"[AIService] _build_payload() called for: {self.name}")
		payload = {}
		if getattr(self, "custom_payload", None):
			payload = self._parse_custom_payload()
		else:
			payload = self._build_default_payload()
		print(f"[AIService] _build_payload() completed for: {self.name}")
		return payload

	def _parse_custom_payload(self):
		"""Parse custom payload from string or dict."""
		try:
			payload = self.custom_payload
			if isinstance(payload, str):
				payload = json.loads(payload)
			print(f"[AIService] _build_payload() Custom payload: {payload}")
			return payload
		except Exception as e:
			print(f"[AIService] _build_payload() Invalid custom_payload: {e!s}")
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
			print(f"[AIService] _build_payload() Default payload: {payload}")
			return payload
		return {}

	def _make_ping_request(self, http_method, headers, payload):
		print(f"[AIService] _make_ping_request() called for: {self.name}")
		print(f"[AIService] _make_ping_request() Making request to: {self.base_url}")
		try:
			resp = self._send_request(http_method, headers, payload)
			return self._handle_response(resp)
		except Exception as e:
			print(f"[AIService] _make_ping_request() Exception: {e!s}")
			frappe.throw(f"Ping failed: {e!s}")

	def _send_request(self, http_method, headers, payload):
		"""Send HTTP request to the AI service."""
		if http_method.upper() == "POST":
			print(f"[AIService] _make_ping_request() POST payload: {payload}")
			return requests.post(self.base_url, headers=headers, json=payload, timeout=30)
		else:
			print("[AIService] _make_ping_request() GET request (no payload)")
			return requests.get(self.base_url, headers=headers, timeout=30)

	def _handle_response(self, resp):
		"""Handle the response from the AI service."""
		print(f"[AIService] _make_ping_request() Response status: {resp.status_code}")
		print(f"[AIService] _make_ping_request() Response text: {resp.text}")

		if resp.status_code < 400:
			try:
				data = resp.json()
				msg = frappe._("Ping successful! Response: ") + str(data)
			except Exception:
				msg = frappe._("Ping successful! Status: ") + str(resp.status_code)
			print(f"[AIService] _make_ping_request() Success: {msg}")
			frappe.msgprint(msg, title=frappe._("Ping Result"), indicator="green")
			print(f"[AIService] _make_ping_request() completed for: {self.name}")
			return msg
		else:
			print(
				f"[AIService] _make_ping_request() Ping failed. Status: {resp.status_code}, Response: {resp.text}"
			)
			frappe.throw(f"Ping failed. Status: {resp.status_code}, Response: {resp.text}")

	@frappe.whitelist()
	def test_connection(self):
		"""
		Test connection for this AI Service instance. Shows user-friendly message instead of raw response.
		"""
		headers = self._build_headers()
		print(f"[AIService] test_connection() headers main: {headers}")
		url = self.base_url + self.base_url_suffix
		http_method = getattr(self, "http_method", None) or "POST"
		payload = self._build_test_payload()

		try:
			resp = self._send_test_request(url, headers, payload, http_method)
			return self._handle_test_response(resp)
		except requests.exceptions.Timeout:
			return self._handle_timeout_error()
		except requests.exceptions.ConnectionError:
			return self._handle_connection_error()
		except Exception as e:
			return self._handle_generic_error(e)

	def _build_test_payload(self):
		"""Build payload for connection test."""
		if getattr(self, "model_name", None):
			return {
				"model": self.model_name,
				"max_tokens": 100,
				"messages": [
					{
						"role": "user",
						"content": "Ping test from Frappe. Please respond with 'pong' or similar.",
					}
				],
			}
		return {}

	def _send_test_request(self, url, headers, payload, http_method):
		"""Send test request to the AI service."""
		if http_method.upper() == "POST":
			resp = requests.post(url, headers=headers, json=payload, timeout=30)
			print(f"[AIService] test_connection() resp: {resp}")
		else:
			resp = requests.get(url, headers=headers, timeout=30)
		return resp

	def _handle_test_response(self, resp):
		"""Handle test response from the AI service."""
		if resp.status_code == 200:
			service_name = self.service_type or "AI Service"
			msg = f"✅ Connection established successfully with {service_name}"
			frappe.msgprint(msg, title=frappe._("Connection Test"), indicator="green")
			return {"status": "success", "message": msg}
		else:
			return self._handle_test_error(resp)

	def _handle_test_error(self, resp):
		"""Handle test error response."""
		error_msg = self._get_error_message(resp.status_code)
		frappe.msgprint(error_msg, title=frappe._("Connection Test"), indicator="red")
		return {"status": "error", "message": error_msg}

	def _get_error_message(self, status_code):
		"""Get user-friendly error message based on status code."""
		if status_code == 401:
			return "❌ Authentication failed. Please check your credentials."
		elif status_code == 403:
			return "❌ Access denied. Please check your API permissions."
		elif status_code == 404:
			return "❌ Service not found. Please check the URL."
		elif status_code >= 500:
			return "❌ Server error. Please try again later."
		else:
			return f"❌ Connection failed with status code: {status_code}"

	def _handle_timeout_error(self):
		"""Handle timeout error."""
		error_msg = "❌ Connection timeout. Please check your network or try again."
		frappe.msgprint(error_msg, title=frappe._("Connection Test"), indicator="red")
		return {"status": "error", "message": error_msg}

	def _handle_connection_error(self):
		"""Handle connection error."""
		error_msg = "❌ Connection failed. Please check the URL and network connectivity."
		frappe.msgprint(error_msg, title=frappe._("Connection Test"), indicator="red")
		return {"status": "error", "message": error_msg}

	def _handle_generic_error(self, e):
		"""Handle generic error."""
		error_msg = f"❌ Connection test failed: {e!s}"
		frappe.msgprint(error_msg, title=frappe._("Connection Test"), indicator="red")
		print(f"[AIService] test_connection() Exception: {e!s}")
		return {"status": "error", "message": error_msg}


@frappe.whitelist()
def ping(docname):
	doc = frappe.get_doc("AI Service", docname)
	return doc.ping()


@frappe.whitelist()
def test_connection(docname):
	doc = frappe.get_doc("AI Service", docname)
	return doc.test_connection()
