if ngx.status ~= 200 then
    if ngx.var.upstream_addr ~= nil and ngx.var.cube_retcode == "310200" then
        local utils = require "utils"
        local ok, err = utils:is_faulty_backend(ngx.var.backend_ip, false)
        if err then
            ngx.log(ngx.ERR, "LEVEL_WARN||", "check backend fault err: ", err)
        end
        if ok == true then
            ngx.var.cube_retcode = "340" .. ngx.status
        else
            ngx.var.cube_retcode = "330" .. ngx.status
        end
    end
end

ngx.header["X-Cube-Request-Id"] = ngx.var.http_x_cube_request_id
ngx.header["X-Cube-Retcode"] = ngx.var.cube_retcode
