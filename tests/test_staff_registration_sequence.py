import pytest
from test_message_deletion import inbox_api

@pytest.mark.parametrize('role',['librarian','superadmin'])
def test_staff_registration_and_catalog_sequence(inbox_api,role):
    client,headers,factory=inbox_api
    data=dict(username='new-patron',email='new@evsu.edu.ph',first_name='New',last_name='Patron',password='Password1!',confirm_password='Password1!',account_type='student',department='Computing')
    assert client.post('/admin/users/patrons',headers=headers['student'],json=data).status_code==403
    response=client.post('/admin/users/patrons',headers=headers[role],json=data)
    assert response.status_code==201, response.text
    assert response.json()['role']=='student'
    assert client.post('/admin/users/patrons',headers=headers[role],json=data).status_code==409
    data['account_type']='superadmin'
    assert client.post('/admin/users/patrons',headers=headers[role],json=data).status_code==422
    payload=dict(title='Sequence',author='Author',category='Test',total_copies=1,accession_no='0001-0001')
    first=client.post('/admin/books',headers=headers[role],json=payload)
    assert first.status_code==201, first.text
    payload.pop('accession_no')
    second=client.post('/admin/books',headers=headers[role],json=payload)
    assert second.json()['accession_no']=='0001-0002'
    path=f"/admin/books/{first.json()['id']}"
    before=client.get(path+'/copies',headers=headers[role]).json()
    assert client.put(path,headers=headers[role],json={'accession_no':'0001-0002'}).status_code==400
    assert client.put(path,headers=headers[role],json={'accession_no':'0001-0009'}).status_code==200
    assert client.get(path+'/copies',headers=headers[role]).json()==before
