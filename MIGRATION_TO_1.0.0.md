# Migration to VintaSend 1.0.0

## Supporting One-off Notifications

One-off notifications allow sending notifications directly to email addresses or phone numbers without requiring user IDs from your database. This is perfect for welcome emails, marketing campaigns, and external party communications.

### Backend Implementation Guide

External backend packages need to implement one-off notification persistence alongside regular notifications:

```python
class YourBackend(BaseNotificationBackend):
    
    def persist_one_off_notification(
        self,
        email_or_phone: str,
        first_name: str,
        last_name: str,
        notification_type: str,
        title: str,
        body_template: str,
        context_name: str,
        context_kwargs: NotificationContextDict,
        send_after: datetime.datetime | None = None,
        subject_template: str = "",
        preheader_template: str = "",
        adapter_extra_parameters: dict | None = None,
        attachments: list[NotificationAttachment] | None = None,
    ) -> OneOffNotification:
        """Create and store a one-off notification"""
        
        # 1. Store attachments if any
        stored_attachments = self._store_attachments(attachments or [])
        
        # 2. Create one-off notification record
        one_off_notification = YourOneOffNotificationModel(
            id=self._generate_id(),
            email_or_phone=email_or_phone,
            first_name=first_name,
            last_name=last_name,
            notification_type=notification_type,
            title=title,
            body_template=body_template,
            subject_template=subject_template,
            preheader_template=preheader_template,
            context_name=context_name,
            context_kwargs=context_kwargs,
            send_after=send_after,
            adapter_extra_parameters=adapter_extra_parameters or {},
            attachments=stored_attachments,
            status="pending" if send_after else "pending",
            created_at=datetime.datetime.now(tz=datetime.timezone.utc),
            updated_at=datetime.datetime.now(tz=datetime.timezone.utc),
        )
        
        # 3. Save to your storage system
        self._save_one_off_notification(one_off_notification)
        
        return OneOffNotification(
            id=one_off_notification.id,
            email_or_phone=email_or_phone,
            first_name=first_name,
            last_name=last_name,
            notification_type=notification_type,
            title=title,
            body_template=body_template,
            subject_template=subject_template,
            preheader_template=preheader_template,
            context_name=context_name,
            context_kwargs=context_kwargs,
            send_after=send_after,
            adapter_extra_parameters=adapter_extra_parameters or {},
            attachments=stored_attachments,
            status=one_off_notification.status,
            created_at=one_off_notification.created_at,
            updated_at=one_off_notification.updated_at,
        )
    
    def get_all_pending_notifications(self) -> Iterable[Notification | OneOffNotification]:
        """Return both regular notifications and one-off notifications that are pending"""
        # 1. Get regular pending notifications
        regular_notifications = self._get_pending_regular_notifications()
        
        # 2. Get pending one-off notifications  
        one_off_notifications = self._get_pending_one_off_notifications()
        
        # 3. Combine and return
        return list(regular_notifications) + list(one_off_notifications)
    
    def get_notification(self, notification_id: int | str | uuid.UUID) -> Notification | OneOffNotification:
        """Get notification by ID, supporting both regular and one-off notifications"""
        # Try regular notification first
        try:
            return self._get_regular_notification(notification_id)
        except NotificationNotFoundError:
            pass
        
        # Try one-off notification
        try:
            return self._get_one_off_notification(notification_id)
        except NotificationNotFoundError:
            pass
        
        raise NotificationNotFoundError(f"Notification {notification_id} not found")

    def _get_one_off_notification(self, notification_id: int | str | uuid.UUID) -> OneOffNotification:
        """Retrieve one-off notification from storage"""
        # Implement based on your storage system
        one_off_record = self._fetch_one_off_from_storage(notification_id)
        
        return OneOffNotification(
            id=one_off_record.id,
            email_or_phone=one_off_record.email_or_phone,
            first_name=one_off_record.first_name,
            last_name=one_off_record.last_name,
            notification_type=one_off_record.notification_type,
            title=one_off_record.title,
            body_template=one_off_record.body_template,
            subject_template=one_off_record.subject_template,
            preheader_template=one_off_record.preheader_template,
            context_name=one_off_record.context_name,
            context_kwargs=one_off_record.context_kwargs,
            send_after=one_off_record.send_after,
            adapter_extra_parameters=one_off_record.adapter_extra_parameters,
            attachments=self._deserialize_attachments(one_off_record.attachments),
            status=one_off_record.status,
            created_at=one_off_record.created_at,
            updated_at=one_off_record.updated_at,
        )
```

### Adapter Implementation Guide

Adapters need to handle both regular notifications and one-off notifications. The key difference is how recipient information is extracted:

```python
class YourEmailAdapter(BaseNotificationAdapter):
    
    def send(self, notification: "Notification | OneOffNotification", context: "NotificationContextDict") -> None:
        """Send notification - works for both regular and one-off notifications"""
        
        # 1. Get recipient information based on notification type
        recipient_info = self._get_recipient_info(notification)
        
        # 2. Render email content
        email_content = self.template_renderer.render(notification, context)
        
        # 3. Create and send email
        email_message = self._create_email_message(notification, email_content, recipient_info)
        self._attach_files(email_message, notification.attachments)
        self._send_email(email_message)
    
    def _get_recipient_info(self, notification: "Notification | OneOffNotification") -> dict:
        """Extract recipient information from notification"""
        
        if isinstance(notification, OneOffNotification):
            # One-off notification: use provided email/phone and name
            return {
                "email_or_phone": notification.email_or_phone,
                "first_name": notification.first_name,
                "last_name": notification.last_name,
                "full_name": f"{notification.first_name} {notification.last_name}".strip(),
            }
        else:
            # Regular notification: fetch user information
            user = self._get_user_by_id(notification.user_id)
            return {
                "email_or_phone": user.email,  # or user.phone for SMS
                "first_name": user.first_name,
                "last_name": user.last_name,
                "full_name": user.get_full_name(),
            }
    
    def _create_email_message(self, notification, email_content, recipient_info):
        """Create email message with proper recipient handling"""
        
        email_message = self.email_client.create_message()
        
        # Set recipient based on notification type
        if notification.notification_type == "EMAIL":
            email_message.to = recipient_info["email_or_phone"]
            email_message.to_name = recipient_info["full_name"]
        
        # Set content
        email_message.subject = email_content.subject
        email_message.html_body = email_content.body
        if hasattr(email_content, 'preheader'):
            email_message.preheader = email_content.preheader
        
        return email_message

# For SMS adapters
class YourSMSAdapter(BaseNotificationAdapter):
    
    def send(self, notification: "Notification | OneOffNotification", context: "NotificationContextDict") -> None:
        """Send SMS - works for both regular and one-off notifications"""
        
        # Get phone number based on notification type
        if isinstance(notification, OneOffNotification):
            phone_number = notification.email_or_phone
            recipient_name = f"{notification.first_name} {notification.last_name}".strip()
        else:
            user = self._get_user_by_id(notification.user_id)
            phone_number = user.phone
            recipient_name = user.get_full_name()
        
        # Render and send SMS
        sms_content = self.template_renderer.render(notification, context)
        self._send_sms(phone_number, sms_content.body, recipient_name)
```

### Template Renderer Implementation Guide

Template renderers may need to be aware of one-off notifications for context generation:

```python
class YourTemplateRenderer(BaseTemplateRenderer):
    
    def render(self, notification: "Notification | OneOffNotification", context: "NotificationContextDict"):
        """Render templates with enhanced context for one-off notifications"""
        
        # 1. Start with provided context
        template_context = dict(context)
        
        # 2. Add recipient information to context
        if isinstance(notification, OneOffNotification):
            # Add one-off notification recipient info
            template_context.update({
                "recipient": {
                    "first_name": notification.first_name,
                    "last_name": notification.last_name,
                    "full_name": f"{notification.first_name} {notification.last_name}".strip(),
                    "email_or_phone": notification.email_or_phone,
                },
                "is_one_off": True,
            })
        else:
            # For regular notifications, you might want to add user info
            # This depends on your existing implementation
            template_context.update({
                "user_id": notification.user_id,
                "is_one_off": False,
            })
        
        # 3. Render templates as usual
        body = self._render_template(notification.body_template, template_context)
        subject = self._render_template(notification.subject_template, template_context)
        preheader = self._render_template(notification.preheader_template, template_context)
        
        return RenderedEmailContent(
            body=body,
            subject=subject,
            preheader=preheader,
        )

# For Django template renderer example
class DjangoTemplateRenderer(BaseTemplateRenderer):
    
    def render(self, notification: "Notification | OneOffNotification", context: "NotificationContextDict"):
        """Django-specific implementation"""
        from django.template import Context, Template
        
        # Build context
        template_context = dict(context)
        
        if isinstance(notification, OneOffNotification):
            template_context["recipient"] = {
                "first_name": notification.first_name,
                "last_name": notification.last_name,
                "email_or_phone": notification.email_or_phone,
            }
        
        django_context = Context(template_context)
        
        # Render each template
        body_template = Template(self._load_template(notification.body_template))
        subject_template = Template(notification.subject_template)
        preheader_template = Template(notification.preheader_template)
        
        return RenderedEmailContent(
            body=body_template.render(django_context),
            subject=subject_template.render(django_context),
            preheader=preheader_template.render(django_context),
        )
```

### Database Schema Considerations

When implementing one-off notifications, consider your database schema:

```sql
-- Example table for one-off notifications
CREATE TABLE one_off_notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email_or_phone VARCHAR(255) NOT NULL,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    notification_type VARCHAR(50) NOT NULL,
    title VARCHAR(255) NOT NULL,
    body_template TEXT NOT NULL,
    subject_template TEXT,
    preheader_template TEXT,
    context_name VARCHAR(100) NOT NULL,
    context_kwargs JSONB NOT NULL DEFAULT '{}',
    send_after TIMESTAMP WITH TIME ZONE,
    adapter_extra_parameters JSONB NOT NULL DEFAULT '{}',
    attachments JSONB NOT NULL DEFAULT '[]',
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    sent_at TIMESTAMP WITH TIME ZONE,
    failed_at TIMESTAMP WITH TIME ZONE,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX idx_one_off_notifications_status ON one_off_notifications(status);
CREATE INDEX idx_one_off_notifications_send_after ON one_off_notifications(send_after);
CREATE INDEX idx_one_off_notifications_created_at ON one_off_notifications(created_at);
```

### Key Migration Points for One-off Notifications

1. **Backward Compatibility**: One-off notifications are completely additive - existing functionality is unchanged
2. **Unified Interface**: Adapters should handle both `Notification` and `OneOffNotification` transparently
3. **Template Context**: Consider what additional context one-off notifications might need
4. **Storage Strategy**: Decide if you want separate tables or a unified approach
5. **Querying**: Update queries to include both regular and one-off notifications where appropriate

### Testing One-off Notifications

```python
class TestOneOffNotifications:
    """Test one-off notification functionality"""
    
    def test_create_one_off_notification(self):
        """Test creating one-off notification"""
        notification = self.backend.persist_one_off_notification(
            email_or_phone="test@example.com",
            first_name="John",
            last_name="Doe",
            notification_type="EMAIL",
            title="Welcome Email",
            body_template="welcome.html",
            context_name="welcome_context",
            context_kwargs={"company": "Acme Corp"},
        )
        
        assert notification.email_or_phone == "test@example.com"
        assert notification.first_name == "John"
        assert notification.last_name == "Doe"
        assert notification.status == "pending"
    
    def test_send_one_off_notification(self):
        """Test sending one-off notification"""
        # Test that adapters handle one-off notifications correctly
        pass
    
    def test_one_off_with_attachments(self):
        """Test one-off notifications with attachments"""
        pass
    
    def test_mixed_pending_notifications(self):
        """Test querying both regular and one-off pending notifications"""
        pass
```

## Supporting Attachments in Notifications

### Backend Implementation Guide

External backend packages need to implement attachment storage. Here's the pattern:

```python
class YourBackend(BaseNotificationBackend):
    
    def persist_notification(
        self,
        # ... existing parameters ...
        attachments: list[NotificationAttachment] | None = None,
    ) -> Notification:
        # 1. Store attachments using your preferred method
        stored_attachments = self._store_attachments(attachments or [])
        
        # 2. Create notification with stored attachments
        notification = YourNotificationModel(
            # ... existing fields ...
            attachments=stored_attachments,  # Serialize as needed
        )
        
        return notification
    
    def _store_attachments(self, attachments: list[NotificationAttachment]) -> list[StoredAttachment]:
        """Implement based on your storage strategy"""
        stored_attachments = []
        
        for attachment in attachments:
            # Read file data
            file_data = self._read_attachment_data(attachment.file)
            
            # Store using your method (filesystem, S3, database, etc.)
            storage_path = self._store_file_data(file_data, attachment.filename)
            
            # Create your AttachmentFile implementation
            attachment_file = YourAttachmentFile(storage_path, your_config)
            
            # Create StoredAttachment
            stored_attachment = StoredAttachment(
                id=generate_id(),
                filename=attachment.filename,
                content_type=attachment.content_type,
                size=len(file_data),
                checksum=calculate_checksum(file_data),
                created_at=datetime.datetime.now(tz=datetime.timezone.utc),
                description=attachment.description,
                is_inline=attachment.is_inline,
                storage_metadata={"your": "metadata"},
                file=attachment_file
            )
            
            stored_attachments.append(stored_attachment)
        
        return stored_attachments

class YourAttachmentFile(AttachmentFile):
    """Your specific implementation for file access"""
    
    def __init__(self, storage_path: str, config):
        self.storage_path = storage_path
        self.config = config
    
    def read(self) -> bytes:
        # Implement based on your storage (read from filesystem, S3, etc.)
        pass
    
    def stream(self) -> BinaryIO:
        # Return stream for large files
        pass
    
    def url(self, expires_in: int = 3600) -> str:
        # Generate temporary URL if supported
        pass
    
    def delete(self) -> None:
        # Delete from your storage
        pass
```

### Adapter Implementation Guide

Email adapters need to handle attachments when sending:

```python
class YourEmailAdapter(BaseNotificationAdapter):
    
    def send(self, notification: "Notification | OneOffNotification", context: "NotificationContextDict") -> None:
        # 1. Render email content as usual
        email_content = self.template_renderer.render(notification, context)
        
        # 2. Create email message
        email_message = self._create_email_message(notification, email_content)
        
        # 3. Attach files
        self._attach_files(email_message, notification.attachments)
        
        # 4. Send email
        self._send_email(email_message)
    
    def _attach_files(self, email_message, attachments: list[StoredAttachment]) -> None:
        """Attach files to email message"""
        for attachment in attachments:
            try:
                if attachment.is_inline:
                    # Handle inline attachments (for HTML emails)
                    file_data = attachment.get_file_data()
                    self._attach_inline(email_message, attachment, file_data)
                else:
                    # Handle regular attachments
                    file_data = attachment.get_file_data()
                    self._attach_regular(email_message, attachment, file_data)
            except Exception as e:
                # Log error but continue sending email
                logger.warning(f"Failed to attach {attachment.filename}: {e}")
    
    def _attach_regular(self, email_message, attachment: StoredAttachment, file_data: bytes) -> None:
        """Implement based on your email library"""
        email_message.attach(
            filename=attachment.filename,
            content=file_data,
            content_type=attachment.content_type
        )
    
    def _attach_inline(self, email_message, attachment: StoredAttachment, file_data: bytes) -> None:
        """Implement inline attachments for HTML emails"""
        cid = f"attachment_{attachment.id}"
        email_message.attach_inline(
            filename=attachment.filename,
            content=file_data,
            content_type=attachment.content_type,
            content_id=cid
        )
```

### Key Migration Points

1. **Backward Compatibility**: All changes are additive - existing code continues to work
2. **Optional Attachments**: The `attachments` parameter defaults to `None`/empty list
3. **Error Handling**: Attachment failures shouldn't break notification sending
4. **Storage Flexibility**: Each backend can implement storage however it wants
5. **Testing**: Use the updated stub implementations for testing

### Common Patterns

1. **File Storage**: Local filesystem, S3, Azure Blob, Google Cloud Storage
2. **Database Storage**: Store file metadata in DB, files in storage service
3. **Inline Images**: Support for embedding images in HTML emails
4. **Size Limits**: Implement appropriate file size limits
5. **Security**: Validate file types and scan for malware

## Test Implementation Plan

### Core Functionality Tests

```python
class TestNotificationAttachments:
    """Test attachment functionality with fake implementations"""
    
    def test_create_notification_with_file_path_attachment(self):
        """Test creating notification with file path"""
        pass
    
    def test_create_notification_with_bytesio_attachment(self):
        """Test creating notification with BytesIO object"""
        pass
    
    def test_create_notification_with_url_attachment(self):
        """Test creating notification with URL"""
        pass
    
    def test_one_off_notification_with_attachments(self):
        """Test one-off notifications with attachments"""
        pass
    
    def test_attachment_validation(self):
        """Test file type and size validation"""
        pass
    
    def test_attachment_file_access(self):
        """Test StoredAttachment file access methods"""
        pass
    
    def test_email_adapter_with_attachments(self):
        """Test email sending with attachments"""
        pass
    
    def test_inline_attachments(self):
        """Test inline attachment support"""
        pass
    
    def test_attachment_deletion(self):
        """Test attachment cleanup"""
        pass
    
    def test_backward_compatibility(self):
        """Test that existing code without attachments still works"""
        pass

class TestFakeBackendAttachments:
    """Test fake backend attachment storage"""
    
    def test_store_and_retrieve_attachments(self):
        """Test attachment storage and retrieval"""
        pass
    
    def test_attachment_metadata_persistence(self):
        """Test attachment metadata storage"""
        pass
    
    def test_attachment_file_interface(self):
        """Test AttachmentFile interface methods"""
        pass

class TestFakeAdapterAttachments:
    """Test fake adapter attachment handling"""
    
    def test_email_adapter_captures_attachments(self):
        """Test that fake adapter captures attachment info"""
        pass
    
    def test_attachment_error_handling(self):
        """Test adapter behavior when attachments fail"""
        pass
```

### Integration Tests

```python
class TestAttachmentIntegration:
    """End-to-end attachment tests"""
    
    def test_full_notification_flow_with_attachments(self):
        """Test complete flow from creation to sending"""
        notification_service = NotificationService(
            notification_adapters=[
                ("vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter",
                 "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer")
            ],
            notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend"
        )
        
        # Create notification with various attachment types
        notification = notification_service.create_notification(
            user_id=123,
            notification_type="EMAIL",
            title="Test with attachments",
            body_template="test_template.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "value"}),
            attachments=[
                NotificationAttachment(
                    file=BytesIO(b"test pdf content"),
                    filename="test.pdf",
                    content_type="application/pdf"
                ),
                NotificationAttachment(
                    file="/path/to/test/image.jpg",
                    filename="image.jpg",
                    is_inline=True
                )
            ]
        )
        
        # Verify attachments were stored and are accessible
        assert len(notification.attachments) == 2
        assert notification.attachments[0].filename == "test.pdf"
        assert notification.attachments[1].is_inline == True
        
        # Verify file access works
        pdf_data = notification.attachments[0].get_file_data()
        assert pdf_data == b"test pdf content"
```
