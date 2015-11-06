from . import xml_utils
from . import http
from . import utils

from .exceptions import make_exception

from .models import *

import urlparse
import urllib


class _Base(object):
    def __init__(self, auth, endpoint, is_cname, session):
        self.auth = auth
        self.endpoint = _normalize_endpoint(endpoint)
        self.session = session or http.Session()

        self._make_url = _UrlMaker(self.endpoint, is_cname)

    def _do(self, method, bucket_name, object_name, **kwargs):
        req = http.Request(method, self._make_url(bucket_name, object_name), **kwargs)
        self.auth.sign_request(req, bucket_name, object_name)

        resp = self.session.do_request(req)
        if resp.status // 100 != 2:
            raise make_exception(resp)

        return resp

    def _parse_result(self, resp, parse_func, klass):
        result = klass(resp)
        parse_func(result, resp.read())
        return result


class Service(_Base):
    def __init__(self, auth, endpoint,
                 session=None):
        super(Service, self).__init__(auth, endpoint, False, session)

    def list_buckets(self, prefix='', marker='', max_keys=100):
        resp = self._do('GET', '', '',
                        params={'prefix': prefix,
                                'marker': marker,
                                'max-keys': max_keys})
        return self._parse_result(resp, xml_utils.parse_list_buckets, ListBucketsResult)


class Bucket(_Base):
    def __init__(self, auth, endpoint, bucket_name,
                 is_cname=False,
                 session=None):
        super(Bucket, self).__init__(auth, endpoint, is_cname, session)
        self.bucket_name = bucket_name

    def sign_url(self, method, object_name, expires, headers=None, params=None):
        req = http.Request(method, self._make_url(self.bucket_name, object_name),
                           headers=headers,
                           params=params)
        return self.auth.sign_url(req, self.bucket_name, object_name, expires)

    def list_objects(self, prefix='', delimiter='', marker='', max_keys=100):
        resp = self.__do_object('GET', '',
                                params={'prefix': prefix,
                                        'delimiter': delimiter,
                                        'marker': marker,
                                        'max-keys': max_keys,
                                        'encoding-type': 'url'})
        return self._parse_result(resp, xml_utils.parse_list_objects, ListObjectsResult)

    def put_object(self, object_name, data, headers=None):
        headers = utils.set_content_type(http.CaseInsensitiveDict(headers), object_name)

        resp = self.__do_object('PUT', object_name, data=data, headers=headers)
        return PutObjectResult(resp)

    def append_object(self, object_name, position, data, headers=None):
        headers = utils.set_content_type(http.CaseInsensitiveDict(headers), object_name)

        resp = self.__do_object('POST', object_name,
                                data=data,
                                headers=headers,
                                params={'append': '', 'position': str(position)})
        return AppendObjectResult(resp)

    def get_object(self, object_name, headers=None):
        resp = self.__do_object('GET', object_name, headers=headers)
        return GetObjectResult(resp)

    def head_object(self, object_name):
        resp = self.__do_object('HEAD', object_name)
        return RequestResult(resp)

    def copy_object(self, source_bucket_name, source_object_name, target_object_name, headers=None):
        headers = http.CaseInsensitiveDict(headers)
        headers['x-oss-copy-source'] = '/' + source_bucket_name + '/' + source_object_name

        resp = self.__do_object('PUT', target_object_name, headers=headers)
        return PutObjectResult(resp)

    def delete_object(self, object_name):
        resp = self.__do_object('DELETE', object_name)
        return RequestResult(resp)

    def batch_delete_objects(self, objects, quiet=False):
        data = xml_utils.to_batch_delete_objects_request(objects, quiet, 'url')
        resp = self.__do_object('POST', '',
                                data=data,
                                params={'delete': ''},
                                headers={'Content-MD5': utils.content_md5(data)})
        return self._parse_result(resp, xml_utils.parse_batch_delete_objects, BatchDeleteObjectsResult)

    def init_multipart_upload(self, object_name, headers=None):
        headers = utils.set_content_type(http.CaseInsensitiveDict(headers), object_name)

        resp = self.__do_object('POST', object_name, params={'uploads': ''}, headers=headers)
        return self._parse_result(resp, xml_utils.parse_init_multipart_upload, InitMultipartUploadResult)

    def upload_part(self, object_name, upload_id, part_number, data, headers=None):
        resp = self.__do_object('PUT', object_name,
                                params={'uploadId': upload_id, 'partNumber': str(part_number)},
                                data=data,
                                headers=headers)
        return PutObjectResult(resp)

    def complete_multipart_upload(self, object_name, upload_id, parts, headers=None):
        data = xml_utils.to_complete_upload_request(parts)
        resp = self.__do_object('POST', object_name,
                                params={'uploadId': upload_id},
                                data=data,
                                headers=headers)
        return PutObjectResult(resp)

    def abort_multipart_upload(self, object_name, upload_id, headers=None):
        resp = self.__do_object('DELETE', object_name,
                                params={'uploadId': upload_id},
                                headers=headers)
        return RequestResult(resp)

    def list_multipart_uploads(self,
                               prefix='',
                               delimiter='',
                               key_marker='',
                               upload_id_marker='',
                               max_uploads=1000):
        resp = self.__do_object('GET',
                                params={'uploads': '',
                                        'prefix': prefix,
                                        'delimiter': delimiter,
                                        'key-marker': key_marker,
                                        'upload-id-marker': upload_id_marker,
                                        'max-uploads': max_uploads,
                                        'encoding-type': 'url'})
        return self._parse_result(resp, xml_utils.parse_list_multipart_uploads, ListMultipartUploadsResult)

    def list_parts(self, object_name, upload_id,
                   marker=''):
        resp = self.__do_object('GET', object_name,
                                params={'uploadId': upload_id, 'part-number-marker': marker})
        return self._parse_result(resp, xml_utils.parse_list_parts, ListPartsResult)

    def create_bucket(self, permission):
        resp = self.__do_bucket('PUT', headers={'x-oss-acl': permission})
        return RequestResult(resp)

    def delete_bucket(self):
        resp = self.__do_bucket('DELETE')
        return RequestResult(resp)

    def put_bucket_acl(self, permission):
        resp = self.__do_bucket('PUT', headers={'x-oss-acl': permission}, params={'acl': ''})
        return RequestResult(resp)

    def get_bucket_acl(self):
        resp = self.__do_bucket('GET', params={'acl': ''})
        return self._parse_result(resp, xml_utils.parse_get_bucket_acl, GetBucketAclResult)

    def put_bucket_logging(self, target_bucket, target_prefix):
        data = xml_utils.to_put_bucket_logging(target_bucket, target_prefix)
        resp = self.__do_bucket('PUT', data=data, params={'logging': ''})
        return RequestResult(resp)

    def get_bucket_logging(self):
        resp = self.__do_bucket('GET', params={'logging': ''})
        return self._parse_result(resp, xml_utils.parse_get_bucket_logging, GetBucketLoggingResult)

    def put_bucket_lifecycle(self, data):
        resp = self.__do_bucket('PUT', params={'lifecycle': ''},
                                data=data)
        return RequestResult(resp)

    def get_bucket_lifecycle(self):
        resp = self.__do_bucket('GET', params={'lifecycle': ''})
        return BucketResult(resp)

    def delete_bucket_lifecycle(self):
        resp = self.__do_bucket('DELETE', params={'lifecycle': ''})
        return RequestResult(resp)

    def __do_object(self, method, object_name, **kwargs):
        return self._do(method, self.bucket_name, object_name, **kwargs)

    def __do_bucket(self, method, **kwargs):
        return self._do(method, self.bucket_name, '', **kwargs)



def _normalize_endpoint(endpoint):
    if not endpoint.startswith('http://') and not endpoint.startswith('https://'):
        return 'http://' + endpoint
    else:
        return endpoint


_ENDPOINT_TYPE_ALIYUN = 0
_ENDPOINT_TYPE_CNAME = 1
_ENDPOINT_TYPE_IP = 2


def _determine_endpoint_type(netloc, is_cname):
    if utils.is_ip_or_localhost(netloc):
        return _ENDPOINT_TYPE_IP

    if is_cname:
        return _ENDPOINT_TYPE_CNAME
    else:
        return _ENDPOINT_TYPE_ALIYUN


class _UrlMaker(object):
    def __init__(self, endpoint, is_cname):
        p = urlparse.urlparse(endpoint)

        self.scheme = p.scheme
        self.netloc = p.netloc
        self.type = _determine_endpoint_type(p.netloc, is_cname)

    def __call__(self, bucket_name, object_name):
        object_name = urllib.quote(object_name)

        if self.type == _ENDPOINT_TYPE_CNAME:
            return '{}://{}/{}'.format(self.scheme, self.netloc, object_name)

        if self.type == _ENDPOINT_TYPE_IP:
            assert bucket_name
            return '{}://{}/{}/{}'.format(self.scheme, self.netloc, bucket_name, object_name)

        if not bucket_name:
            assert not object_name
            return '{}://{}'.format(self.scheme, self.netloc)

        return '{}://{}.{}/{}'.format(self.scheme, bucket_name, self.netloc, object_name)