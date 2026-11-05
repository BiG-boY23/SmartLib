from test_message_deletion import inbox_api

def test_accessions_reject_padding_aliases_and_existing_copies(inbox_api):
    client, headers, factory = inbox_api
    auth=headers['librarian']
    payload=dict(title='Unique',author='Author',category='Test',total_copies=1,accession_no='0010')
    first=client.post('/admin/books',headers=auth,json=payload)
    assert first.status_code==201, first.text
    for number in ['0010','00010','10']:
        payload['accession_no']=number
        assert client.post('/admin/books',headers=auth,json=payload).status_code==409
    copies=client.get(f"/admin/books/{first.json()['id']}/copies",headers=auth).json()
    payload['accession_no']=str(int(copies[0]['accession_no']))
    assert client.post('/admin/books',headers=auth,json=payload).status_code==409
    payload['accession_no']='0001-0001'
    assert client.post('/admin/books',headers=auth,json=payload).status_code==201
    payload['accession_no']='1-1'
    assert client.post('/admin/books',headers=auth,json=payload).status_code==409
