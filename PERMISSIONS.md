# Permission System Documentation (HOMEPAGE)

## Overview
HOMEPAGE has a three-tier role-based permission system designed for family-safe content management.

## User Roles

### 1. **Admin**
- **Who**: The person who sets up the server
- **Permissions**:
  - Full access to all features
  - Can manage all users and change their roles
  - Can view and modify any user's permissions
  - Access to admin panel at `/admin/users`
  - Can create parent and child accounts

### 2. **Parent**
- **Who**: Adult family members
- **Permissions**:
  - Can create and manage child accounts
  - Can set content policies for their children
  - Full posting and following capabilities
  - Access to family management at `/admin/family`
  - Cannot modify other parents or admin accounts

### 3. **Child**
- **Who**: Children with supervised accounts
- **Permissions**: Controlled by their parent
  - Content filter level (strict/moderate/relaxed)
  - Posting permission (can be disabled)
  - External following permission (can follow users outside the family)
  - Daily screen time limits (in minutes)

## Permission Settings

### Content Filter Levels
- **Strict**: Most restrictive, very limited content
- **Moderate**: Balanced filtering (default for new children)
- **Relaxed**: Minimal filtering

### Posting Permission
- Controls whether a child can create posts
- Default: Enabled

### External Following
- Controls whether a child can follow users outside the family/node
- Default: Disabled for safety

### Screen Time Limits
- Daily limit in minutes
- `null` = unlimited
- Tracked per day (implementation for tracking coming soon)

## Database Schema

### New User Fields
```sql
role VARCHAR DEFAULT 'parent'              -- admin, parent, child
parent_id INTEGER REFERENCES users(id)     -- Links child to parent
content_filter_level VARCHAR DEFAULT 'moderate'
can_post BOOLEAN DEFAULT TRUE
can_follow_external BOOLEAN DEFAULT FALSE
max_daily_screen_time INTEGER              -- Minutes, null = unlimited
```

## API Endpoints

### Admin Routes
- `GET /admin/users` - View all users (admin only)
- `POST /admin/users/{user_id}/update-role` - Change user role (admin only)
- `GET /admin/family` - Manage children (parent/admin)
- `POST /admin/users/{user_id}/update-permissions` - Update child permissions
- `POST /admin/create-child` - Create a child account

## Usage Examples

### Creating a Child Account
1. Navigate to `/admin/family`
2. Fill in the "Create Child Account" form
3. Child will be automatically linked to you as parent
4. Default permissions: moderate filter, can post, cannot follow external

### Managing Child Permissions
1. Go to `/admin/family`
2. Find your child in the list
3. Adjust their settings:
   - Content filter level
   - Screen time limit
   - Toggle posting permission
   - Toggle external following
4. Click "Update Permissions"

### Admin User Management
1. Navigate to `/admin/users` (admin only)
2. View all users and their roles
3. Change roles using the dropdown (auto-saves)
4. View permission summaries for child accounts

## Permission Checks in Code

### Example: Require Admin
```python
from app import permissions

@router.get("/admin/something")
async def admin_route(current_user: User = Depends(get_current_user)):
    permissions.require_admin(current_user)
    # ... admin logic
```

### Example: Check if User Can Post
```python
if permissions.can_post(user):
    # Allow post creation
else:
    raise HTTPException(403, "Posting disabled by parent")
```

### Example: Check Management Permission
```python
if permissions.can_manage_user(manager, target_user):
    # Allow modification
```

## Migration

The database migration has been completed. All existing users:
- Default role: `parent`
- Existing admins (is_admin=True) → role set to `admin`
- All new fields added with safe defaults

## Future Enhancements

1. **Screen Time Tracking**: Implement actual usage tracking
2. **Content Filtering**: Apply filter levels to feed content
3. **Activity Reports**: Parents can view children's activity
4. **Scheduled Access**: Time-based restrictions (e.g., no access after 9 PM)
5. **Approval Workflows**: Parents approve children's posts before publishing
6. **Friend Lists**: Children can only interact with approved friends

## Security Notes

- Parents can only manage their own children
- Admins can manage anyone
- Children cannot change their own permissions
- Role changes require admin privileges
- All permission checks happen server-side
