import axios from 'axios'
import { ElLoading } from 'element-plus'
import Message from '../utils/Message'
import router from '@/router'
const contentTypeForm = 'multipart/form-data'
const contentTypeJson = 'application/json'
const responseTypeJson = 'json'
let loading = null;
const instance = axios.create({
    withCredentials: true,
    baseURL: import.meta.env.PROD ? "/admin-api" : "/api",
    timeout: 10 * 1000,
});

instance.interceptors.request.use(
    (config) => {
        if (config.showLoading) {
            loading = ElLoading.service({
                lock: true,
                text: '加载中......',
                background: 'rgba(0, 0, 0, 0.7)',
            });
        }
        return config;
    },
    (error) => {
        if (error.config?.showLoading && loading) {
            loading.close();
        }
        Message.error("请求发送失败");
        return Promise.reject("请求发送失败");
    }
);

instance.interceptors.response.use(
    async (response) => {
        const { showLoading, errorCallback, showError = true, responseType } = response.config;
        if (showLoading && loading) {
            loading.close()
        }
        const responseData = response.data;
        if (responseType == "arraybuffer" || responseType == "blob") {
            return responseData;
        }

        if (responseData.code == 200) {
            return responseData;
        } else if (responseData.code == 901) {
            try {
                await instance.post('/account/logout', new FormData())
            } catch {

            }
            router.push('/login')
            return Promise.reject({ showError: false });
        } else {

            if (errorCallback) {
                errorCallback(responseData);
            }
            return Promise.reject({ showError: showError, msg: responseData.info });
        }
    },
    (error) => {
        if (error.config?.showLoading && loading) {
            loading.close();
        }
        return Promise.reject({ showError: true, msg: "网络异常" })
    }
);

const request = (config) => {
    const { url, params, dataType, method = 'post', showLoading = true, responseType = responseTypeJson, showError = true, sensitiveConfirmPwd } = config;
    const normalizedMethod = String(method).toLowerCase();
    let contentType = contentTypeForm;
    if (dataType != null && dataType == 'json') {
        contentType = contentTypeJson;
    }
    let formData = params;
    if (contentType === contentTypeForm) {
        formData = new FormData();
        for (let key in (params || {})) {
            formData.append(key, params[key] == undefined ? "" : params[key]);
        }
    }
    let headers = {
        'X-Requested-With': 'XMLHttpRequest',
    }
    if (typeof sensitiveConfirmPwd === 'string' && sensitiveConfirmPwd) {
        headers['X-Admin-Confirm-Pwd'] = sensitiveConfirmPwd
    }

    if (contentType === contentTypeJson) {
        headers['Content-Type'] = contentTypeJson;
    }
    return instance.request({
        url,
        method: normalizedMethod,
        data: normalizedMethod === 'get' ? undefined : formData,
        params: normalizedMethod === 'get' ? (params || {}) : undefined,
        onUploadProgress: (event) => {
            if (config.uploadProgressCallback) {
                config.uploadProgressCallback(event);
            }
        },
        responseType: responseType,
        headers: headers,
        showLoading: showLoading,
        errorCallback: config.errorCallback,
        showError: showError,
        timeout: config.timeout,
    }).catch(error => {
        if (error.showError) {
            Message.error(error.msg);
        }
        return null;
    });
};
export default request;
