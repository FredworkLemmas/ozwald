I have created a comprehensive prompt for an AI assistant to implement the Ozwald secrets management system. This proposal incorporates the following key architectural decisions:

- **Redis-Backed Persistence**: Encrypted secrets are stored in Redis using a "Vault and Locker" model at the Realm level. This leverages Ozwald's existing infrastructure for frontend-backend communication.
- **Token-Based Decryption**: Secrets are encrypted/decrypted using user-provided tokens that are never stored by Ozwald, ensuring that sensitive data is only "unlocked" during service activation.
- **Docker `env-file` Integration**: Decrypted secrets are injected into containers using the `--env-file` option. The provisioner will handle the creation of temporary environment files and ensure their secure cleanup after the container starts.

The output document is formatted according to the requested convention, including Goal, Notes, Background Info, Instructions, and a Builder Summary.

### Builder Summary (included in the response)
This document provides a comprehensive instruction set for an AI assistant to design and implement a secrets management system for Ozwald. This plan utilizes **Redis** as the secure, transient storage for encrypted secrets and specifies that secrets must be injected into Docker containers using the **`env-file`** mechanism.

The architecture follows a "Vault and Locker" model at the Realm level:
- **Redis Persistence**: Secrets are encrypted and stored in Redis using unique keys per locker.
- **Dynamic Decryption**: The backend retrieves and decrypts these secrets only when a service is activated, using tokens provided by the user via the API.
- **Secure Runtime Injection**: Upon activation, decrypted secrets are written to a temporary environment file and passed to the Docker container via the `--env-file` flag.
- **Cleanup**: The provisioner is responsible for the secure deletion of these temporary files immediately after use.
- **Integrated Workflow**: New API endpoints allow for updating secrets, and existing service update flows are extended to pass decryption tokens safely.

---

### Goal
The goal is to implement a secure, Redis-backed secrets management system for Ozwald. This system will allow users to securely store encrypted environment variables (secrets) and inject them into containerized services at runtime using decryption tokens and the Docker `env-file` mechanism.

### Background Info
Ozwald is a provisioner for container-based systems, primarily focused on AI services. It uses a well-typed configuration (YAML) to define "Realms", "Services", "Profiles", and "Varieties". 

A key component of Ozwald's architecture is its use of Redis to share state between the Provisioner API and the backend daemon. By using Redis for secrets storage, we ensure that secrets are available across the system without relying on shared file systems, while maintaining security through encryption at the application level.

### Notes
These notes capture the refined architecture for Ozwald's secrets management:

*   **Redis-Centric Storage**:
    *   Secrets will be stored as encrypted blobs in Redis.
    *   Redis keys should follow a predictable naming convention: `vault:<realm>:<locker_name>`.
    *   This eliminates the need for managing encrypted files on disk and simplifies synchronization between the frontend and backend.
*   **The "Vault and Locker" Model**:
    *   Each **Realm** in the Ozwald config should have an optional `vault` section.
    *   A `vault` contains multiple `lockers`.
    *   A `locker` is a named logical container for a set of secrets (key-value pairs).
    *   Service definitions will specify which lockers they require.
*   **Decryption Tokens**:
    *   Secrets are encrypted using a token provided by the user.
    *   Ozwald does **not** store the decryption token. 
    *   The token must be provided during the `update-active-services` request to "unlock" the secrets for the containers being started.
*   **API Extensions**:
    *   `POST /srv/secrets/update/`: Accepts `realm`, `locker_name`, a `token`, and the secrets payload. Encrypts the payload and stores it in Redis.
    *   `POST /srv/services/active/update/`: Extended to accept a mapping of locker names to tokens.
*   **Secure `env-file` Injection**:
    *   When the backend starts a service, it fetches the encrypted blob from Redis.
    *   It uses the provided token to decrypt the blob into environment variables.
    *   These variables are written to a temporary file on the host.
    *   The path to this file is passed to `docker run` using the `--env-file` option.
    *   The temporary file must be deleted immediately after the container is launched.
*   **Future Proofing**: 
    *   The use of Redis makes this system ready for the future Ozwald orchestrator.
    *   The `env-file` approach is standard and avoids leaking secrets into the process environment or command line history.

### Instructions for the AI Assistant
Please create a detailed implementation plan for the Ozwald Secrets Management system based on the goal, background, and notes provided above. Your plan should include:

1.  **Model Updates (`src/orchestration/models.py`)**:
    *   Define `Vault` and `Locker` Pydantic models.
    *   Add `vault` to the `Realm` model.
    *   Update `ServiceInformation` or the API request payload to include a `secrets_tokens` field (a dictionary mapping locker names to tokens).

2.  **Configuration Handling (`src/config/reader.py`)**:
    *   Update `ConfigReader` to parse the `vault` and `locker` definitions from the YAML config.
    *   Add logic to associate services with their required lockers.

3.  **Redis Secret Store (`src/util/secrets_store.py`)**:
    *   Create a new utility class `SecretsStore` that interacts with Redis.
    *   Implement `set_secret(realm, locker, encrypted_blob)` and `get_secret(realm, locker)`.
    *   Reuse the existing Redis connection logic from `ActiveServicesCache`.

4.  **Encryption/Decryption Utility (`src/util/crypto.py`)**:
    *   Implement a wrapper around `cryptography.fernet` (or a similar robust library) for encrypting and decrypting payloads using user-provided tokens.

5.  **API Implementation (`src/api/provisioner.py`)**:
    *   Add the `update_secrets` endpoint.
    *   Modify `update_active_services` to capture tokens and pass them to the `SystemProvisioner`.

6.  **Provisioning Logic (`src/orchestration/provisioner.py` & `src/services/container.py`)**:
    *   Update `SystemProvisioner` to retrieve secrets from the `SecretsStore` and decrypt them using the provided tokens during the service startup sequence.
    *   Update `ContainerService.start()` to:
        *   Write the decrypted secrets to a temporary `env-file`.
        *   Modify the Docker command generation to include the `--env-file` flag pointing to this temporary file.
        *   Implement a robust cleanup mechanism (e.g., using a `try...finally` block or a context manager) to delete the temporary file after `docker run` is called.

7.  **CLI Updates (`src/command/ozwald.py`)**:
    *   Add a new command: `ozwald secrets set <realm> <locker> --token <token> --file <json_file>`.
    *   Update `ozwald update-services` to allow providing tokens for specific lockers.

8.  **Testing Strategy**:
    *   Unit tests for encryption/decryption logic and temporary file handling.
    *   Integration tests involving Redis and Docker to verify that secrets are correctly injected into the container and that temporary files are cleaned up.