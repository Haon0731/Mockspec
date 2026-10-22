   
               
        
                          
        
   
import pytest
from flask.helpers import abort


@pytest.mark.parametrize("scenario,has_app_context,code,args,kwargs", [
    (
        "app_context_exists_404",
        True,
        404,
        (),
        {}
    ),
    (
        "app_context_exists_500_with_message",
        True,
        500,
        ("Internal Server Error",),
        {}
    ),
    (
        "no_app_context_403",
        False,
        403,
        (),
        {}
    ),
    (
        "no_app_context_with_args_kwargs",
        False,
        400,
        ("Bad Request",),
        {"description": "Invalid input"}
    ),
    (
        "app_context_custom_code",
        True,
        418,
        (),
        {"description": "I'm a teapot"}
    ),
])
def test_abort_scenarios(mocker, scenario, has_app_context, code, args, kwargs):
       
                    

       
                                              
                                                             
                                          
                                                       
                                                

         
                          
                                     
                                    
       
                                 
                      
    # === PY-SLOT-START:mock_stub_001 ===
    pass
    # === PY-SLOT-END:mock_stub_001 ===

                            
    # === PY-SLOT-START:mock_stub_002 ===
    pass
    # === PY-SLOT-END:mock_stub_002 ===

                                   
    # === PY-SLOT-START:mock_stub_003 ===
    pass
    # === PY-SLOT-END:mock_stub_003 ===

                        
                         
    # === PY-SLOT-START:mock_stub_004 ===
    pass
    # === PY-SLOT-END:mock_stub_004 ===

    if has_app_context:
                       
        mock_ctx.app = mock_app
        mock_app.aborter = mock_aborter
                                
        # === PY-SLOT-START:mock_stub_005 ===
        pass
        # === PY-SLOT-END:mock_stub_005 ===

                            
    # === PY-SLOT-START:mock_stub_006 ===
    pass
    # === PY-SLOT-END:mock_stub_006 ===
                      

                                 
    with pytest.raises(Exception) as exc_info:
        abort(code, *args, **kwargs)

                        
    # === PY-SLOT-START:verify_001 ===
    pass
    # === PY-SLOT-END:verify_001 ===

                       
    if has_app_context:
                          
        # === PY-SLOT-START:verify_002 ===
        pass
        # === PY-SLOT-END:verify_002 ===
                                                 
        # === PY-SLOT-START:verify_003 ===
        pass
        # === PY-SLOT-END:verify_003 ===
        assert f"Aborted with {code}" in str(exc_info.value)
    else:
                             
        # === PY-SLOT-START:verify_004 ===
        pass
        # === PY-SLOT-END:verify_004 ===
        assert f"Werkzeug abort {code}" in str(exc_info.value)
