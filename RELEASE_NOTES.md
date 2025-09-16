# Release Notes

## Version 1.0.0 (2025-09-16)

### 🚀 Major Features

#### File Attachments Support
- **NEW**: Added comprehensive file attachment support for notifications
- **Multiple Input Types**: Support for file paths, URLs, bytes data, file-like objects, and Path objects  
- **URL Downloads**: Automatic download of remote files from HTTP/HTTPS, S3, Google Cloud Storage, and Azure Blob Storage URLs
- **Content Type Detection**: Automatic MIME type detection based on file extensions
- **Inline Attachments**: Support for inline images in HTML emails with `is_inline` flag
- **Backend Integration**: New storage interfaces for backends to implement attachment persistence
- **Adapter Integration**: Updated adapter interfaces to handle attachments in email sending

#### One-Off Notifications
- **NEW**: Send notifications directly to email addresses or phone numbers without requiring user IDs
- **Direct Targeting**: Use email addresses or phone numbers as direct targets
- **Use Cases**: Perfect for welcome emails, marketing campaigns, and external party notifications
- **Full Feature Support**: One-off notifications support all standard features including attachments, scheduling, and templating

### 🔧 API Enhancements

#### Notification Service
- Added `attachments` parameter to `create_notification()` method
- Added `attachments` parameter to `create_one_off_notification()` method  
- Added `attachments` parameter to `update_notification()` method
- New `create_one_off_notification()` method for direct email/phone targeting
- Enhanced AsyncIO support for all new features

#### Data Classes
- **NEW**: `NotificationAttachment` class for defining file attachments
- **NEW**: `StoredAttachment` class for backend-stored attachment metadata
- **NEW**: `OneOffNotification` class for non-user-targeted notifications
- **NEW**: `FileAttachment` type alias supporting multiple input formats
- **NEW**: `AttachmentFile` abstract base class for stored file access

#### Backend Interfaces
- Added attachment storage methods to `BaseNotificationBackend`
- Added one-off notification persistence to backend interfaces
- Enhanced AsyncIO backend interfaces with attachment support
- New abstract methods for attachment lifecycle management

#### Adapter Interfaces  
- Enhanced adapter interfaces to handle attachments in notification sending
- Updated template renderer interfaces for attachment-aware rendering
- Backward compatible changes with optional attachment parameters

### 🔄 Backward Compatibility
- All existing APIs remain fully functional
- Optional attachment parameters maintain backward compatibility
- Existing notifications continue to work without modification
- No breaking changes to core interfaces

### 🧪 Testing & Quality
- Comprehensive test suite for attachment functionality (1300+ test lines)
- Tests for all file input types and edge cases
- AsyncIO and sync testing coverage
- Validation and error handling test cases
- End-to-end attachment workflow testing

### 📚 Documentation
- Updated README with attachment examples and usage patterns
- New glossary entries for attachments and one-off notifications
- AsyncIO examples for all new features
- Import statements updated for new classes

### 🔧 Dependencies & Infrastructure
- Updated setuptools dependency for security improvements
- Enhanced type hints and type safety
- Improved error handling and validation
- Added comprehensive docstrings for new features

### 📋 Migration Guide
For backend and adapter package maintainers:
- See `MIGRATION_TO_1.0.0.md` for detailed implementation guidance
- New abstract methods need implementation in external packages
- Stub implementations provided as reference
- Backward compatibility maintained for gradual migration

---

## Version 0.1.4 (Initial Release)

Initial version of VintaSend with core notification functionality.
