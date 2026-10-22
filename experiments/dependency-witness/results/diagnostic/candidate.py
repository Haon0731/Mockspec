   
               
        
                          
        
   
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
       
                    

       
                                              
                                                             
                                          
                                                       
                                                

         
                          
                                     
                                    
       
                                 
                      
    mock_query = mocker.patch("flask.helpers._cv_app")
    mock_query.get.return_value = None
    mock_fallback = mocker.patch("flask.helpers._wz_abort", return_value=None)

                            
    pass

                                   
    pass

                        
                         
    pass

    if has_app_context:
                       
        mock_ctx.app = mock_app
        mock_app.aborter = mock_aborter
                                
        pass

                            
    pass
                      

                                 
    with pytest.raises(Exception) as exc_info:
        abort(code, *args, **kwargs)

                        
    pass

                       
    if has_app_context:
                          
        pass
                                                 
        pass
        assert f"Aborted with {code}" in str(exc_info.value)
    else:
                             
        pass
        assert f"Werkzeug abort {code}" in str(exc_info.value)
