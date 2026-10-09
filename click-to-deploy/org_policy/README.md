# Project Configuration - APIs & Policy Management

This repository uses a structured approach to configure Google Cloud project resources (Organization Policies and enabled APIs) **before** the primary demo deployment is executed.

This process ensures that the target Google Cloud project is properly governed and has all necessary services enabled prior to the main Terraform configuration running (e.g., resources defined in your separate `demo` folder)

---

## 1. CRITICAL INSTRUCTION: Your Only Edit ⚠️

### ⚠️ CRITICAL: Your Only Edit
**`project_config.json` is the ONLY file you should modify.**
This template provides a comprehensive library of constraints and APIs. For your specific demo, you **must keep only the required items** and remove everything else from this file.

---

### 🛑 DO NOT EDIT `project_resource.tf`
This file contains the core mechanism to apply your settings. Modifying it may break the dynamic policy application.

---

## 2. Structure of `project_config.json`

The file is a standard JSON object containing two key arrays: `constraints` for policies and `apis_to_enable` for services. **Important:** Prune this file to include only the resources your demo actually uses.

### `project_config.json` Example

#### If Policy mutation & API enable is required for the Demo:

```json
{
  "constraints": [
    {
      "constraint": "compute.vmExternalIpAccess",
      "policy_type": "list",
      "allow_all": false,
      "deny_all": true,
      "allowed_values": [],
      "denied_values": [],
      "restore_post_deployment": true 
    },
    {
      "constraint": "compute.skipDefaultNetworkCreation",
      "policy_type": "boolean",
      "enforced": true,
      "restore_post_deployment": false 
    }
  ],
  "apis_to_enable": [
    "bigquery.googleapis.com", 
    "cloudfunctions.googleapis.com",
    "pubsub.googleapis.com"
  ]
}
```

#### If Policy mutation & APIs are not required for the Demo:

```json
{
  "constraints": [
  ],
  "apis_to_enable": [
    ""
  ]
}
```
---

## 3. Configuring Organization Policies (`constraints`)

The `constraints` array governs project governance. You define how the policy should be enforced and whether it should be restored post deployment using the **`restore_post_deployment`** flag.

### A. The `restore_post_deployment` Flag

This flag determines the persistence of the policy mutation after the demo deployment is complete.

| Value | Effect | Context |
| :--- | :--- | :--- |
| **`true`** | The policy will be **restored** to its original state (Inherit Parent Policy) after the demo deployment. | Use for **temporary overrides** needed *only* during the setup phase as demo prerequisites. |
| **`false`** | The policy will **remain mutated** (enforced as configured here) after the demo deployment. | Use for **permanent baseline project** so that demo users can user coresponding services later. |

### B. Defining Policy Types

* **List Policy (`"policy_type": "list"`):** Use for policies that allow or deny specific values (e.g., domains, service accounts). You control enforcement using `allow_all`, `deny_all`, `allowed_values`, and `denied_values`.

* **Boolean Policy (`"policy_type": "boolean"`):** Use for simple on/off switches for a constraint. You control enforcement using the boolean value of the **`enforced`** field.

---

## 4. Configuring Enabled APIs (`apis_to_enable`)

This array lists the Google Cloud APIs that **must be enabled** on the project before the demo deployment proceeds.

Add the API's **service name** (e.g., `servicename.googleapis.com`) as a string item to the list. You can find the service name for any GCP API in the API Library documentation.

---