import requests

# 1. Login
r = requests.post('http://localhost:8000/api/v1/auth/login/', json={
    'email': 'tu@email.com',
    'password': 'tupassword'
})
print('LOGIN:', r.status_code, r.json())
token = r.json().get('tokens', {}).get('access') or r.json().get('access')
print('TOKEN:', token)

# 2. Ver lives
r2 = requests.get('http://localhost:8000/api/v1/livestreams/', headers={
    'Authorization': f'Bearer {token}'
})
print('LIVES:', r2.status_code, r2.json())
