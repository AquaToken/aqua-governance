def load_all_records(request_builder, start_cursor=None, page_size=200):
    base_request_builder = request_builder.limit(page_size)
    cursor = start_cursor
    while True:
        if cursor:
            request_builder = base_request_builder.cursor(cursor)
        else:
            request_builder = base_request_builder

        response = request_builder.call()
        records = response['_embedded']['records']

        for record in records:
            yield record
            cursor = record['paging_token']

        if len(records) == 0:
            break

        # TODO: Roll back after the patch is released. https://github.com/stellar/go/pull/5032
        # if len(records) < page_size:
        #     break
